import json
import hashlib
import logging
import os
import re
import secrets
import subprocess
from contextlib import closing
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import base64
from io import BytesIO
import os
import serpapi
import requests
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None
try:
    import mysql.connector
    from mysql.connector import Error as MySQLError
except ImportError:
    mysql = None
    MySQLError = Exception

app = FastAPI(title="Mono Product Landing Generator")
logger = logging.getLogger("mono_product")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup_init_database() -> None:
    try:
        init_database()
        print(f"[startup] Database {DB_NAME} ready")
    except Exception as exc:
        print(f"[startup] Database initialization failed: {exc}")

PROJECT_DIR = Path(__file__).resolve().parent.parent
angular_browser_dir = PROJECT_DIR / "front-end" / "dist" / "mono-product-angular" / "browser"
UPLOAD_DIR = PROJECT_DIR / "backend" / "uploads" / "product_images"

DEFAULT_IMAGE = "https://images.unsplash.com/photo-1516321318423-f06f85e504b3?auto=format&fit=crop&w=1200&q=80"
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5"
DEFAULT_OPENAI_VISION_MODEL = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o-mini")
IMAGE_SIMILARITY_THRESHOLD = float(os.environ.get("IMAGE_SIMILARITY_THRESHOLD", "80"))


def load_env_file() -> None:
    for env_path in (PROJECT_DIR / ".env", Path(__file__).resolve().parent / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


load_env_file()


DB_NAME = os.environ.get("DB_NAME", "mono_product_stage")
DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("DB_PORT", "3306")),
    "user": os.environ.get("DB_USER", "root"),
    "password": os.environ.get("DB_PASSWORD", ""),
}
MYSQL_CLI = os.environ.get("MYSQL_CLI", r"C:\xampp\mysql\bin\mysql.exe")
DEFAULT_ADMIN = {
    "nom": os.environ.get("ADMIN_NAME", "Administrateur"),
    "email": os.environ.get("ADMIN_EMAIL", "admin@localhost.test"),
    # Keep the raw password value here (may be empty). When seeding the DB we will
    # compute a secure PBKDF2 hash and insert the hashed password. If no ADMIN_PASSWORD
    # is provided we default to a usable password 'admin123' so the admin account is
    # immediately loginable during development.
    "motDePasse": os.environ.get("ADMIN_PASSWORD", ""),
}
DEFAULT_ADMIN_EMAILS = list(dict.fromkeys([DEFAULT_ADMIN["email"], "admin@local.test", "admin@localhost.test"]))


class ProductMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.description = ""
        self.image = ""
        self.meta: Dict[str, str] = {}
        self.json_ld_blocks: List[str] = []
        self.text_chunks: List[str] = []
        self.prices: List[str] = []
        self.availability: List[str] = []
        self.ratings: List[str] = []
        self.product_details: List[str] = []
        self._current_tag = ""
        self._capture_json_ld = False
        self._json_ld = ""

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        attrs_dict = {key.lower(): value for key, value in attrs if value is not None}
        self._current_tag = tag.lower()

        if self._current_tag == "meta":
            key = (attrs_dict.get("property") or attrs_dict.get("name") or "").lower()
            content = clean_text(attrs_dict.get("content", ""))
            if key and content:
                self.meta[key] = content
                if key in {"description", "og:description", "twitter:description"}:
                    self.description = self.description or content
                if key in {"og:title", "twitter:title"}:
                    self.title = self.title or content
                if key in {"og:image", "og:image:url", "twitter:image", "twitter:image:src", "image"}:
                    self.image = self.image or content

        if self._current_tag == "script" and attrs_dict.get("type", "").lower() == "application/ld+json":
            self._capture_json_ld = True
            self._json_ld = ""

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._capture_json_ld:
            if self._json_ld.strip():
                self.json_ld_blocks.append(self._json_ld.strip())
            self._capture_json_ld = False
            self._json_ld = ""
        self._current_tag = ""

    def handle_data(self, data: str) -> None:
        if self._capture_json_ld:
            self._json_ld += data
            return

        text = clean_text(data)
        if not text:
            return

        if self._current_tag == "title":
            self.title += text
        elif self._current_tag in {"h1", "h2", "h3", "p", "li", "span", "div", "article", "section"} and len(text) > 2:
            self.text_chunks.append(text)
            # Extract prices, availability, and ratings from text
            if re.search(r'\d+(?:[,.]\d{1,2})?\s*(?:€|eur|mad|dh|usd|\$|£)', text, flags=re.I):
                price = re.search(r'\d+(?:[,.]\d{1,2})?\s*(?:€|eur|mad|dh|usd|\$|£)', text).group(0)
                if price not in self.prices:
                    self.prices.append(price)
            if re.search(r'disponib|stock|available|rupture|épuisé', text, flags=re.I):
                if text not in self.availability:
                    self.availability.append(text)
            if re.search(r'(?:note|rating|avis|évaluation|★|note)', text, flags=re.I):
                if text not in self.ratings:
                    self.ratings.append(text)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = unescape(str(value))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def unique(values: Iterable[str]) -> List[str]:
    seen = set()
    output: List[str] = []
    for value in values:
        value = clean_text(value)
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            output.append(value)
    return output


def db_identifier(value: str) -> str:
    identifier = clean_text(value)
    if not re.fullmatch(r"[A-Za-z0-9_]+", identifier):
        raise HTTPException(status_code=500, detail="Nom de base de donnees invalide.")
    return identifier

def require_mysql_connector() -> None:
    if mysql is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "Module MySQL manquant et fallback indisponible. Installez mysql-connector-python "
                "ou verifiez le chemin MYSQL_CLI vers mysql.exe de XAMPP."
            ),
        )


def sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    text = str(value)
    text = text.replace("\\", "\\\\").replace("\0", "\\0").replace("'", "''")
    return f"'{text}'"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash or "$" not in stored_hash:
        return False
    try:
        algorithm, salt, digest = stored_hash.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()
    return secrets.compare_digest(candidate, digest)


def validate_auth_payload(nom: str = "", email: str = "", password: str = "", require_name: bool = False) -> None:
    if require_name and len(clean_text(nom)) < 2:
        raise HTTPException(status_code=400, detail="Nom requis.")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", clean_text(email)):
        raise HTTPException(status_code=400, detail="Email invalide.")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Le mot de passe doit contenir au moins 6 caracteres.")


def mysql_cli_command(use_database: bool = True) -> List[str]:
    command = [
        MYSQL_CLI,
        "-h",
        DB_CONFIG["host"],
        "-P",
        str(DB_CONFIG["port"]),
        "-u",
        DB_CONFIG["user"],
        "--batch",
        "--raw",
        "--skip-column-names",
    ]
    if DB_CONFIG["password"]:
        command.append(f"-p{DB_CONFIG['password']}")
    if use_database:
        command.extend(["-D", DB_NAME])
    return command


def run_mysql_cli(sql: str, use_database: bool = True) -> str:
    try:
        result = subprocess.run(
            mysql_cli_command(use_database),
            input=sql,
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )
    except FileNotFoundError:
        require_mysql_connector()
        return ""
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Execution MySQL impossible avec mysql.exe. Verifiez que MySQL est lance dans XAMPP "
                f"et que .env est correct. Erreur: {exc.stderr.strip() or exc.stdout.strip()}"
            ),
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="MySQL n'a pas repondu dans le delai attendu.")
    return result.stdout.strip()


def connect_mysql(use_database: bool = True):
    require_mysql_connector()
    config = {**DB_CONFIG}
    if use_database:
        config["database"] = DB_NAME
    try:
        return mysql.connector.connect(**config)
    except MySQLError as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Connexion MySQL impossible. Verifiez que MySQL est lance dans XAMPP "
                f"et que la configuration .env est correcte. Erreur: {exc}"
            ),
        )


def init_database() -> None:
    database = db_identifier(DB_NAME)
    if mysql is None:
        run_mysql_cli(
            f"CREATE DATABASE IF NOT EXISTS `{database}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;",
            use_database=False,
        )
        # Prepare a hashed admin password for the CLI path as well. If no password
        # provided via env, fallback to a usable default 'admin123'. We store the
        # hashed PBKDF2 value in the DB so authentication works.
        raw_admin_pw = DEFAULT_ADMIN.get("motDePasse") or os.environ.get("ADMIN_PASSWORD", "")
        if not raw_admin_pw:
            raw_admin_pw = "admin123"
        admin_pw_hash = hash_password(raw_admin_pw)

        run_mysql_cli(
            """
            CREATE TABLE IF NOT EXISTS utilisateurs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                nom VARCHAR(120) NOT NULL,
                email VARCHAR(190) NOT NULL UNIQUE,
                motDePasse VARCHAR(255) NOT NULL DEFAULT '',
                dateCreation TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

            CREATE TABLE IF NOT EXISTS produits (
                id INT AUTO_INCREMENT PRIMARY KEY,
                utilisateur_id INT NULL,
                nom VARCHAR(255) NOT NULL,
                description TEXT NULL,
                prix VARCHAR(120) NULL,
                marque VARCHAR(190) NULL,
                categorie VARCHAR(190) NULL,
                caracteristiquesTechniques LONGTEXT NULL,
                avis LONGTEXT NULL,
                image TEXT NULL,
                images LONGTEXT NULL,
                urlProduit VARCHAR(2048) NOT NULL,
                sourceType VARCHAR(60) NULL,
                similarite DECIMAL(5,2) NULL,
                seoTitle VARCHAR(255) NULL,
                seoDescription TEXT NULL,
                enrichissement LONGTEXT NULL,
                verification LONGTEXT NULL,
                dateCreation TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_url_produit (urlProduit(768)),
                INDEX idx_produits_utilisateur (utilisateur_id),
                CONSTRAINT fk_produits_utilisateur
                    FOREIGN KEY (utilisateur_id) REFERENCES utilisateurs(id)
                    ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

            INSERT INTO utilisateurs (nom, email, motDePasse)
            VALUES {admin_values}
            ON DUPLICATE KEY UPDATE nom = VALUES(nom), motDePasse = VALUES(motDePasse);
            """.format(
                admin_values=", ".join(
                    f"({sql_literal(DEFAULT_ADMIN['nom'])}, {sql_literal(email)}, {sql_literal(admin_pw_hash)})"
                    for email in DEFAULT_ADMIN_EMAILS
                ),
            )
        )
        ensure_product_extra_columns_cli()
        return

    with closing(connect_mysql(use_database=False)) as connection:
        with closing(connection.cursor()) as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        connection.commit()

    with closing(connect_mysql()) as connection:
        with closing(connection.cursor()) as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS utilisateurs (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    nom VARCHAR(120) NOT NULL,
                    email VARCHAR(190) NOT NULL UNIQUE,
                    motDePasse VARCHAR(255) NOT NULL DEFAULT '',
                    dateCreation TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS produits (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    utilisateur_id INT NULL,
                    nom VARCHAR(255) NOT NULL,
                    description TEXT NULL,
                    prix VARCHAR(120) NULL,
                    marque VARCHAR(190) NULL,
                    categorie VARCHAR(190) NULL,
                    caracteristiquesTechniques LONGTEXT NULL,
                    avis LONGTEXT NULL,
                    image TEXT NULL,
                    images LONGTEXT NULL,
                    urlProduit VARCHAR(2048) NOT NULL,
                    sourceType VARCHAR(60) NULL,
                    similarite DECIMAL(5,2) NULL,
                    seoTitle VARCHAR(255) NULL,
                    seoDescription TEXT NULL,
                    enrichissement LONGTEXT NULL,
                    verification LONGTEXT NULL,
                    dateCreation TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_url_produit (urlProduit(768)),
                    INDEX idx_produits_utilisateur (utilisateur_id),
                    CONSTRAINT fk_produits_utilisateur
                        FOREIGN KEY (utilisateur_id) REFERENCES utilisateurs(id)
                        ON DELETE SET NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            # Ensure we insert a hashed password. If the configured admin password
            # is empty, fallback to 'admin123' so the seeded admin can log in.
            raw_admin_pw = DEFAULT_ADMIN.get("motDePasse") or os.environ.get("ADMIN_PASSWORD", "")
            if not raw_admin_pw:
                raw_admin_pw = "admin123"
            admin_pw_hash = hash_password(raw_admin_pw)
            cursor.executemany(
                """
                INSERT INTO utilisateurs (nom, email, motDePasse)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE nom = VALUES(nom), motDePasse = VALUES(motDePasse)
                """,
                [(DEFAULT_ADMIN["nom"], email, admin_pw_hash) for email in DEFAULT_ADMIN_EMAILS],
            )
            ensure_product_extra_columns(cursor)
        connection.commit()


def ensure_product_extra_columns(cursor) -> None:
    """Add enrichment columns for existing installations without dropping data."""
    cursor.execute("SHOW COLUMNS FROM produits")
    existing = {str(row[0]) for row in cursor.fetchall()}
    columns = {
        "prix": "ALTER TABLE produits ADD COLUMN prix VARCHAR(120) NULL AFTER description",
        "marque": "ALTER TABLE produits ADD COLUMN marque VARCHAR(190) NULL AFTER prix",
        "categorie": "ALTER TABLE produits ADD COLUMN categorie VARCHAR(190) NULL AFTER marque",
        "caracteristiquesTechniques": "ALTER TABLE produits ADD COLUMN caracteristiquesTechniques LONGTEXT NULL AFTER categorie",
        "avis": "ALTER TABLE produits ADD COLUMN avis LONGTEXT NULL AFTER caracteristiquesTechniques",
        "sourceType": "ALTER TABLE produits ADD COLUMN sourceType VARCHAR(60) NULL AFTER urlProduit",
        "similarite": "ALTER TABLE produits ADD COLUMN similarite DECIMAL(5,2) NULL AFTER sourceType",
        "seoTitle": "ALTER TABLE produits ADD COLUMN seoTitle VARCHAR(255) NULL AFTER similarite",
        "seoDescription": "ALTER TABLE produits ADD COLUMN seoDescription TEXT NULL AFTER seoTitle",
        "enrichissement": "ALTER TABLE produits ADD COLUMN enrichissement LONGTEXT NULL AFTER seoDescription",
        "verification": "ALTER TABLE produits ADD COLUMN verification LONGTEXT NULL AFTER enrichissement",
    }
    for name, statement in columns.items():
        if name not in existing:
            cursor.execute(statement)


def ensure_product_extra_columns_cli() -> None:
    output = run_mysql_cli("SHOW COLUMNS FROM produits;")
    existing = {line.split("\t")[0] for line in output.splitlines() if line}
    columns = {
        "prix": "ALTER TABLE produits ADD COLUMN prix VARCHAR(120) NULL AFTER description;",
        "marque": "ALTER TABLE produits ADD COLUMN marque VARCHAR(190) NULL AFTER prix;",
        "categorie": "ALTER TABLE produits ADD COLUMN categorie VARCHAR(190) NULL AFTER marque;",
        "caracteristiquesTechniques": "ALTER TABLE produits ADD COLUMN caracteristiquesTechniques LONGTEXT NULL AFTER categorie;",
        "avis": "ALTER TABLE produits ADD COLUMN avis LONGTEXT NULL AFTER caracteristiquesTechniques;",
        "sourceType": "ALTER TABLE produits ADD COLUMN sourceType VARCHAR(60) NULL AFTER urlProduit;",
        "similarite": "ALTER TABLE produits ADD COLUMN similarite DECIMAL(5,2) NULL AFTER sourceType;",
        "seoTitle": "ALTER TABLE produits ADD COLUMN seoTitle VARCHAR(255) NULL AFTER similarite;",
        "seoDescription": "ALTER TABLE produits ADD COLUMN seoDescription TEXT NULL AFTER seoTitle;",
        "enrichissement": "ALTER TABLE produits ADD COLUMN enrichissement LONGTEXT NULL AFTER seoDescription;",
        "verification": "ALTER TABLE produits ADD COLUMN verification LONGTEXT NULL AFTER enrichissement;",
    }
    for name, statement in columns.items():
        if name not in existing:
            run_mysql_cli(statement)


def get_default_admin_id(connection) -> int:
    with closing(connection.cursor()) as cursor:
        cursor.execute("SELECT id FROM utilisateurs WHERE email = %s", (DEFAULT_ADMIN["email"],))
        row = cursor.fetchone()
        if row:
            return int(row[0])

        raw_admin_pw = DEFAULT_ADMIN.get("motDePasse") or os.environ.get("ADMIN_PASSWORD", "")
        if not raw_admin_pw:
            raw_admin_pw = "admin123"
        admin_pw_hash = hash_password(raw_admin_pw)
        cursor.execute(
            "INSERT INTO utilisateurs (nom, email, motDePasse) VALUES (%s, %s, %s)",
            (DEFAULT_ADMIN["nom"], DEFAULT_ADMIN["email"], admin_pw_hash),
        )
        return int(cursor.lastrowid)


def register_user(nom: str, email: str, password: str) -> Dict[str, Any]:
    init_database()
    nom = clean_text(nom)
    email = clean_text(email).lower()
    validate_auth_payload(nom, email, password, require_name=True)
    password_hash = hash_password(password)

    if mysql is None:
        try:
            output = run_mysql_cli(
                """
                INSERT INTO utilisateurs (nom, email, motDePasse)
                VALUES ({nom}, {email}, {password_hash});
                SELECT LAST_INSERT_ID();
                """.format(
                    nom=sql_literal(nom),
                    email=sql_literal(email),
                    password_hash=sql_literal(password_hash),
                )
            )
        except HTTPException as exc:
            if "Duplicate" in str(exc.detail) or "duplicata" in str(exc.detail).lower():
                raise HTTPException(status_code=409, detail="Cet email existe deja.")
            raise
        last_line = output.splitlines()[-1] if output else "0"
        user_id = int(last_line) if last_line.isdigit() else 0
        return {"id": user_id, "nom": nom, "email": email}

    with closing(connect_mysql()) as connection:
        with closing(connection.cursor()) as cursor:
            try:
                cursor.execute(
                    "INSERT INTO utilisateurs (nom, email, motDePasse) VALUES (%s, %s, %s)",
                    (nom, email, password_hash),
                )
            except MySQLError as exc:
                if "Duplicate" in str(exc) or "1062" in str(exc):
                    raise HTTPException(status_code=409, detail="Cet email existe deja.")
                raise
            user_id = int(cursor.lastrowid)
        connection.commit()
    return {"id": user_id, "nom": nom, "email": email}


def authenticate_user(email: str, password: str) -> Dict[str, Any]:
    init_database()
    email = clean_text(email).lower()
    validate_auth_payload(email=email, password=password)

    if mysql is None:
        output = run_mysql_cli(
            """
            SELECT id, nom, email, motDePasse
            FROM utilisateurs
            WHERE email = {email}
            LIMIT 1;
            """.format(email=sql_literal(email))
        )
        if not output:
            raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect.")
        columns = output.splitlines()[0].split("\t")
        if len(columns) < 4 or not verify_password(password, columns[3]):
            raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect.")
        return {"id": int(columns[0]), "nom": columns[1], "email": columns[2]}

    with closing(connect_mysql()) as connection:
        with closing(connection.cursor(dictionary=True)) as cursor:
            cursor.execute(
                "SELECT id, nom, email, motDePasse FROM utilisateurs WHERE email = %s LIMIT 1",
                (email,),
            )
            user = cursor.fetchone()
    if not user or not verify_password(password, user.get("motDePasse", "")):
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect.")
    return {"id": int(user["id"]), "nom": user["nom"], "email": user["email"]}


def require_existing_user_id(user_id: Any) -> int:
    try:
        parsed_id = int(user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Connexion requise pour gerer les produits.")
    if parsed_id <= 0:
        raise HTTPException(status_code=401, detail="Connexion requise pour gerer les produits.")

    init_database()
    if mysql is None:
        output = run_mysql_cli(f"SELECT id FROM utilisateurs WHERE id = {parsed_id} LIMIT 1;")
        if not output:
            raise HTTPException(status_code=401, detail="Utilisateur introuvable. Reconnectez-vous.")
        return parsed_id

    with closing(connect_mysql()) as connection:
        with closing(connection.cursor()) as cursor:
            cursor.execute("SELECT id FROM utilisateurs WHERE id = %s LIMIT 1", (parsed_id,))
            row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Utilisateur introuvable. Reconnectez-vous.")
    return parsed_id


def save_product_to_database(product: Dict[str, Any], utilisateur_id: Any) -> int:
    init_database()
    user_id = require_existing_user_id(utilisateur_id)
    name = clean_text(product.get("name", ""))
    description = clean_text(product.get("description", ""))
    image = clean_text(product.get("image", ""))
    images = product.get("images", [])
    url = clean_text(product.get("url", ""))
    price = clean_text(product.get("price", ""))
    brand = clean_text(product.get("brand", product.get("marque", "")))
    category = clean_text(product.get("category", ""))
    technical_specs = product.get("technicalSpecs", product.get("technical_specs", product.get("caracteristiquesTechniques", [])))
    reviews = product.get("reviews", product.get("avis", []))
    source_type = clean_text(product.get("sourceType", product.get("source_type", "")))
    similarity_score = product.get("similarityScore", product.get("similarity_score"))
    seo = product.get("seo") if isinstance(product.get("seo"), dict) else {}
    seo_title = clean_text(seo.get("title", ""))
    seo_description = clean_text(seo.get("description", ""))
    enrichment_json = json.dumps(
        {
            "tagline": product.get("tagline", ""),
            "features": product.get("features", []),
            "benefits": product.get("benefits", []),
            "faq": product.get("faq", []),
            "ctaTexts": product.get("ctaTexts", []),
            "cta": product.get("cta", ""),
            "sections": product.get("sections", {}),
            "availability": product.get("availability", ""),
            "rating": product.get("rating", ""),
            "brand": brand,
            "category": category,
            "technicalSpecs": technical_specs,
            "reviews": reviews,
            "contentSource": product.get("contentSource", ""),
        },
        ensure_ascii=False,
    )
    verification_json = json.dumps(product.get("verification", {}), ensure_ascii=False)

    validate_product_request(name, url)

    if mysql is None:
        images_json = json.dumps(images if isinstance(images, list) else [], ensure_ascii=False)
        output = run_mysql_cli(
            """
            INSERT INTO produits (
                utilisateur_id, nom, description, image, images, urlProduit,
                prix, marque, categorie, caracteristiquesTechniques, avis,
                sourceType, similarite, seoTitle, seoDescription, enrichissement, verification
            )
            VALUES (
                {user_id},
                {name},
                {description},
                {image},
                {images},
                {url},
                {price},
                {brand},
                {category},
                {technical_specs},
                {reviews},
                {source_type},
                {similarity_score},
                {seo_title},
                {seo_description},
                {enrichment_json},
                {verification_json}
            )
            ON DUPLICATE KEY UPDATE
                id = LAST_INSERT_ID(id),
                utilisateur_id = VALUES(utilisateur_id),
                nom = VALUES(nom),
                description = VALUES(description),
                image = VALUES(image),
                images = VALUES(images),
                prix = VALUES(prix),
                marque = VALUES(marque),
                categorie = VALUES(categorie),
                caracteristiquesTechniques = VALUES(caracteristiquesTechniques),
                avis = VALUES(avis),
                sourceType = VALUES(sourceType),
                similarite = VALUES(similarite),
                seoTitle = VALUES(seoTitle),
                seoDescription = VALUES(seoDescription),
                enrichissement = VALUES(enrichissement),
                verification = VALUES(verification);
            SELECT LAST_INSERT_ID();
            """.format(
                user_id=user_id,
                name=sql_literal(name),
                description=sql_literal(description),
                image=sql_literal(image),
                images=sql_literal(images_json),
                url=sql_literal(url),
                price=sql_literal(price),
                brand=sql_literal(brand),
                category=sql_literal(category),
                technical_specs=sql_literal(json.dumps(technical_specs if isinstance(technical_specs, (list, dict)) else [], ensure_ascii=False)),
                reviews=sql_literal(json.dumps(reviews if isinstance(reviews, (list, dict)) else [], ensure_ascii=False)),
                source_type=sql_literal(source_type),
                similarity_score="NULL" if similarity_score in (None, "") else sql_literal(similarity_score),
                seo_title=sql_literal(seo_title),
                seo_description=sql_literal(seo_description),
                enrichment_json=sql_literal(enrichment_json),
                verification_json=sql_literal(verification_json),
            )
        )
        last_line = output.splitlines()[-1] if output else "0"
        return int(last_line) if last_line.isdigit() else 0

    with closing(connect_mysql()) as connection:
        with closing(connection.cursor()) as cursor:
            ensure_product_extra_columns(cursor)
            cursor.execute(
                """
                INSERT INTO produits (
                    utilisateur_id, nom, description, image, images, urlProduit,
                    prix, marque, categorie, caracteristiquesTechniques, avis,
                    sourceType, similarite, seoTitle, seoDescription, enrichissement, verification
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    utilisateur_id = VALUES(utilisateur_id),
                    nom = VALUES(nom),
                    description = VALUES(description),
                    image = VALUES(image),
                    images = VALUES(images),
                    prix = VALUES(prix),
                    marque = VALUES(marque),
                    categorie = VALUES(categorie),
                    caracteristiquesTechniques = VALUES(caracteristiquesTechniques),
                    avis = VALUES(avis),
                    sourceType = VALUES(sourceType),
                    similarite = VALUES(similarite),
                    seoTitle = VALUES(seoTitle),
                    seoDescription = VALUES(seoDescription),
                    enrichissement = VALUES(enrichissement),
                    verification = VALUES(verification)
                """,
                (
                    user_id,
                    name,
                    description,
                    image,
                    json.dumps(images if isinstance(images, list) else [], ensure_ascii=False),
                    url,
                    price,
                    brand,
                    category,
                    json.dumps(technical_specs if isinstance(technical_specs, (list, dict)) else [], ensure_ascii=False),
                    json.dumps(reviews if isinstance(reviews, (list, dict)) else [], ensure_ascii=False),
                    source_type,
                    similarity_score,
                    seo_title,
                    seo_description,
                    enrichment_json,
                    verification_json,
                ),
            )
            product_id = int(cursor.lastrowid or 0)
            if product_id == 0:
                cursor.execute("SELECT id FROM produits WHERE urlProduit = %s", (url,))
                row = cursor.fetchone()
                product_id = int(row[0]) if row else 0
        connection.commit()
    return product_id


def list_saved_products() -> List[Dict[str, Any]]:
    init_database()
    if mysql is None:
        output = run_mysql_cli(
            """
            SELECT id, nom, description, image, images, urlProduit, DATE_FORMAT(dateCreation, '%Y-%m-%dT%H:%i:%s')
            FROM produits
            ORDER BY dateCreation DESC, id DESC;
            """
        )
        products: List[Dict[str, Any]] = []
        for line in output.splitlines():
            columns = line.split("\t")
            if len(columns) < 7:
                continue
            try:
                images = json.loads(columns[4] or "[]")
            except json.JSONDecodeError:
                images = []
            products.append(
                {
                    "id": int(columns[0]),
                    "nom": columns[1],
                    "description": columns[2],
                    "image": columns[3],
                    "images": images,
                    "urlProduit": columns[5],
                    "dateCreation": columns[6],
                }
            )
        return products

    with closing(connect_mysql()) as connection:
        with closing(connection.cursor(dictionary=True)) as cursor:
            cursor.execute(
                """
                SELECT id, nom, description, image, images, urlProduit, dateCreation
                FROM produits
                ORDER BY dateCreation DESC, id DESC
                """
            )
            rows = cursor.fetchall()

    products: List[Dict[str, Any]] = []
    for row in rows:
        try:
            images = json.loads(row.get("images") or "[]")
        except json.JSONDecodeError:
            images = []
        products.append(
            {
                "id": row.get("id"),
                "nom": row.get("nom"),
                "description": row.get("description"),
                "image": row.get("image"),
                "images": images,
                "urlProduit": row.get("urlProduit"),
                "dateCreation": row.get("dateCreation").isoformat() if row.get("dateCreation") else None,
            }
        )
    return products


def fetch_page(url: str, timeout: int = 15) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
    }
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="ignore")
    except HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Erreur HTTP pendant le scraping : {exc.code}")
    except URLError as exc:
        raise HTTPException(status_code=502, detail=f"Erreur de connexion : {exc.reason}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erreur pendant le chargement de la page : {exc}")


def flatten_json_ld(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        items: List[Dict[str, Any]] = []
        for entry in data:
            items.extend(flatten_json_ld(entry))
        return items
    if not isinstance(data, dict):
        return []

    graph = data.get("@graph")
    items = [data]
    if isinstance(graph, list):
        items.extend(flatten_json_ld(graph))
    return items


def parse_json_ld_blocks(blocks: List[str]) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    for block in blocks:
        try:
            parsed.extend(flatten_json_ld(json.loads(block)))
        except json.JSONDecodeError:
            continue
    return parsed


def find_product_json_ld(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    for item in items:
        item_type = item.get("@type", "")
        if isinstance(item_type, list):
            item_type = " ".join(str(value) for value in item_type)
        if "product" in str(item_type).lower():
            return item
    return items[0] if items else {}


def normalize_image_url(src: str, base_url: str) -> str:
    src = clean_text(src)
    if not src or src.startswith(("data:", "blob:")):
        return ""
    if src.startswith("//"):
        src = "https:" + src
    return urljoin(base_url, src)


def extract_srcset_urls(srcset: str, base_url: str) -> List[str]:
    urls: List[str] = []
    for candidate in srcset.split(","):
        src = candidate.strip().split(" ")[0]
        url = normalize_image_url(src, base_url)
        if url:
            urls.append(url)
    return urls


def extract_images(html: str, base_url: str) -> List[str]:
    images: List[str] = []

    # Patterns to capture all image sources
    patterns = [
        r'(?:src|data-src|data-original|data-lazy-src|content|data-image|data-url)=["\']([^"\']+)["\']',
        r'(?:background-image|background):[^;]*url\(["\']?([^"\')\s]+)["\']?\)',
        r'<source\s[^>]*srcset=["\']([^"\']+)["\']',
        r'url\(["\']?([^"\')\s]+)["\']?\)',
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, html, flags=re.I):
            url = normalize_image_url(match.group(1), base_url)
            if url:
                images.append(url)

    # Extract from srcset and data-srcset attributes
    srcset_pattern = r'(?:srcset|data-srcset)=["\']([^"\']+)["\']'
    for match in re.finditer(srcset_pattern, html, flags=re.I):
        urls = extract_srcset_urls(match.group(1), base_url)
        images.extend(urls)

    # Extract from picture elements
    picture_pattern = r'<picture[^>]*>.*?</picture>'
    for picture_match in re.finditer(picture_pattern, html, flags=re.I | re.DOTALL):
        picture_html = picture_match.group(0)
        source_pattern = r'<source[^>]*srcset=["\']([^"\']+)["\']'
        for source_match in re.finditer(source_pattern, picture_html, flags=re.I):
            urls = extract_srcset_urls(source_match.group(1), base_url)
            images.extend(urls)

    # Extract from image galleries and sliders
    slider_pattern = r'(?:data-image|data-src|data-photo)=["\']([^"\']+)["\']'
    for match in re.finditer(slider_pattern, html, flags=re.I):
        url = normalize_image_url(match.group(1), base_url)
        if url:
            images.append(url)

    # Extract from JSON in script tags (often contains image data)
    json_pattern = r'<script[^>]*type=["\']application/json["\'][^>]*>([^<]+)</script>'
    for match in re.finditer(json_pattern, html, flags=re.I | re.DOTALL):
        try:
            json_data = json.loads(match.group(1))
            images.extend(_extract_urls_from_json(json_data, base_url))
        except json.JSONDecodeError:
            pass

    return unique(images)


def detect_ecommerce_platform(url: str, html: str) -> str:
    """Best-effort platform/source detection for routing, logging, and saved metadata."""
    host = urlparse(url).netloc.lower()
    signals = f"{host}\n{html[:6000].lower()}"
    if "amazon." in host:
        return "amazon"
    if "aliexpress." in host:
        return "aliexpress"
    if "etsy." in host:
        return "etsy"
    if "myshopify.com" in host or "cdn.shopify.com" in signals or "shopify-section" in signals:
        return "shopify"
    if "woocommerce" in signals or "wp-content/plugins/woocommerce" in signals or "single-product" in signals:
        return "woocommerce"
    return "generic"


def _extract_urls_from_json(data: Any, base_url: str, depth: int = 0) -> List[str]:
    """Recursively extract URLs from JSON data"""
    if depth > 5:  # Prevent infinite recursion
        return []

    urls: List[str] = []

    if isinstance(data, dict):
        for key, value in data.items():
            if key.lower() in {'image', 'src', 'url', 'thumbnail', 'photo', 'picture', 'gallery', 'images', 'photos'}:
                if isinstance(value, str):
                    url = normalize_image_url(value, base_url)
                    if url:
                        urls.append(url)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            url = normalize_image_url(item, base_url)
                            if url:
                                urls.append(url)
            urls.extend(_extract_urls_from_json(value, base_url, depth + 1))
    elif isinstance(data, list):
        for item in data:
            urls.extend(_extract_urls_from_json(item, base_url, depth + 1))

    return urls


def select_best_image(images: List[str], fallback: str = "") -> str:
    candidates = [
        image
        for image in unique([fallback, *images])
        if not re.search(r"(logo|icon|sprite|placeholder|avatar|favicon|payment|badge)", image, flags=re.I)
        and not image.lower().endswith((".svg", ".ico"))
    ]
    if not candidates:
        return DEFAULT_IMAGE
    preferred = ("product", "produit", "main", "hero", "large", "detail", "packshot", "media", "photo", "image")
    for image in candidates:
        if any(word in image.lower() for word in preferred):
            return image
    return candidates[0]


def image_tokens(value: str) -> List[str]:
    text = clean_text(value).lower()
    return [
        token
        for token in re.findall(r"[a-z0-9]+", text)
        if len(token) > 1 and token not in {"the", "and", "for", "avec", "pour", "plus", "new"}
    ]


def strong_image_tokens(value: str) -> List[str]:
    generic = {
        "ultra",
        "pro",
        "max",
        "mini",
        "plus",
        "phone",
        "smartphone",
        "product",
        "produit",
    }
    return [
        token
        for token in image_tokens(value)
        if token not in generic and (len(token) >= 4 or any(char.isdigit() for char in token))
    ]


def image_relevance_score(image: str, product_name: str, fallback: str = "") -> int:
    lower = image.lower()
    if re.search(
        r"(logo|icon|sprite|placeholder|avatar|favicon|payment|badge|trade|tradein|trade-in|carrier|financ|compare|setup|support|banner|beacon|metrics|analytics|tracking|pixel|pageview|echo\.png|ads?|advert|promo|newsletter|social|footer|header|loader|spinner|brandmark)",
        lower,
        flags=re.I,
    ):
        return -20
    if lower.endswith((".svg", ".ico", ".gif")):
        return -20
    if re.search(r"([?&](w|width|h|height)=([1-9][0-9]?)(?:&|$))", lower):
        return -12

    product_tokens = image_tokens(product_name)
    strong_tokens = strong_image_tokens(product_name)
    compact_url = re.sub(r"[^a-z0-9]+", "", lower)

    competing_terms = {
        "airpods",
        "galaxy",
        "ipad",
        "iphone",
        "macbook",
        "odyssey",
        "pixel",
        "playstation",
        "xbox",
    }
    expected_terms = set(product_tokens)
    for term in competing_terms - expected_terms:
        if term in compact_url:
            return -30

    score = 0
    compact_name = "".join(product_tokens)
    for token in product_tokens:
        if token in lower:
            score += 4
    for token in strong_tokens:
        if token in compact_url:
            score += 5
    if compact_name and compact_name in compact_url:
        score += 6
    if image == fallback:
        score += 8
    if any(word in lower for word in ("product", "hero", "main", "gallery", "finish", "color", "packshot")):
        score += 3
    if re.search(r"(1200|1500|1600|2000|2048|original|large|xl|zoom)", lower):
        score += 2
    return score


def filter_product_images(images: List[str], product_name: str, fallback: str = "") -> List[str]:
    candidates = unique([fallback, *images])
    scored = [
        (image_relevance_score(image, product_name, fallback), index, image)
        for index, image in enumerate(candidates)
        if image
    ]
    relevant = [(score, index, image) for score, index, image in scored if score > 0]
    pool = relevant if relevant else [(score, index, image) for score, index, image in scored if score > -20]
    pool.sort(key=lambda item: (-item[0], item[1]))
    return [image for _, _, image in pool[:5]] or [DEFAULT_IMAGE]


def extract_price_from_json_ld(product: Dict[str, Any]) -> str:
    offers = product.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        return ""

    price = offers.get("price") or offers.get("lowPrice") or offers.get("highPrice")
    currency = offers.get("priceCurrency") or ""
    if price:
        return clean_text(f"{price} {currency}".strip())
    return ""


def extract_availability_from_json_ld(product: Dict[str, Any]) -> str:
    offers = product.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if isinstance(offers, dict):
        availability = str(offers.get("availability", "")).split("/")[-1]
        return clean_text(availability.replace("_", " "))
    return ""


def extract_rating(product: Dict[str, Any]) -> str:
    rating = product.get("aggregateRating")
    if isinstance(rating, dict):
        value = rating.get("ratingValue")
        count = rating.get("reviewCount") or rating.get("ratingCount")
        if value and count:
            return f"{value}/5 ({count} avis)"
        if value:
            return f"{value}/5"
    return ""


def extract_brand(product: Dict[str, Any], parser: ProductMetadataParser) -> str:
    brand = product.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    if brand:
        return clean_text(brand)
    for key in ("product:brand", "og:brand", "brand", "twitter:data1"):
        if parser.meta.get(key):
            return clean_text(parser.meta[key])
    return ""


def extract_technical_specs(product: Dict[str, Any], html: str, text_chunks: List[str]) -> List[Dict[str, str]]:
    specs: List[Dict[str, str]] = []
    for key in ("sku", "mpn", "gtin", "gtin13", "gtin14", "model", "color", "material", "size"):
        value = clean_text(product.get(key, ""))
        if value:
            specs.append({"label": key.upper(), "value": value})

    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        for row in soup.select("table tr, dl, .product-specs li, .specifications li, [class*=spec] li")[:60]:
            text = clean_text(row.get_text(" ", strip=True))
            if not text or len(text) > 180:
                continue
            if ":" in text:
                label, value = text.split(":", 1)
            else:
                cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td", "dt", "dd"])]
                if len(cells) < 2:
                    continue
                label, value = cells[0], cells[1]
            label = clean_text(label)
            value = clean_text(value)
            if 1 < len(label) <= 60 and value and label.lower() != value.lower():
                specs.append({"label": label, "value": value})

    if len(specs) < 4:
        for chunk in text_chunks:
            if len(specs) >= 8:
                break
            text = clean_text(chunk)
            if ":" not in text or len(text) > 160:
                continue
            label, value = text.split(":", 1)
            if 1 < len(label.strip()) <= 55 and value.strip():
                specs.append({"label": clean_text(label), "value": clean_text(value)})

    unique_specs: List[Dict[str, str]] = []
    seen = set()
    for spec in specs:
        key = (spec.get("label", "").lower(), spec.get("value", "").lower())
        if key not in seen and spec.get("label") and spec.get("value"):
            seen.add(key)
            unique_specs.append(spec)
    return unique_specs[:10]


def extract_reviews(product: Dict[str, Any], parser: ProductMetadataParser) -> List[Dict[str, str]]:
    reviews: List[Dict[str, str]] = []
    json_reviews = product.get("review") or product.get("reviews") or []
    if isinstance(json_reviews, dict):
        json_reviews = [json_reviews]
    if isinstance(json_reviews, list):
        for review in json_reviews[:5]:
            if not isinstance(review, dict):
                continue
            author = review.get("author", "")
            if isinstance(author, dict):
                author = author.get("name", "")
            rating = review.get("reviewRating", {})
            if isinstance(rating, dict):
                rating = rating.get("ratingValue", "")
            reviews.append(
                {
                    "author": clean_text(author) or "Client",
                    "rating": clean_text(rating),
                    "text": clean_text(review.get("reviewBody", "")),
                }
            )

    for rating_text in parser.ratings[:5]:
        reviews.append({"author": "Source", "rating": clean_text(rating_text), "text": ""})

    return [review for review in reviews if review.get("rating") or review.get("text")][:5]


def parse_price(html: str) -> str:
    patterns = [
        r"(?:prix|price|amount)\s*[:\-]?\s*([0-9]+(?:[,.][0-9]{1,2})?\s?(?:€|eur|mad|dh|usd|\$))",
        r"([0-9]+(?:[,.][0-9]{1,2})?\s?(?:€|eur|mad|dh|usd|\$))",
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.I)
        if match:
            return clean_text(match.group(1) if match.lastindex else match.group(0))
    return ""


def default_marketing_features(name: str) -> List[str]:
    product_name = clean_text(name) or "ce produit"
    return [
        f"Presentation claire de {product_name}",
        "Visuels et informations cles mis en avant",
        "Page optimisee pour rassurer et convertir",
        "Appel a l'action relie a la page officielle",
    ]


def is_useful_feature(chunk: str, name: str, description: str) -> bool:
    text = clean_text(chunk)
    if not text:
        return False

    lower = text.lower()
    blocked_exact = {
        "apple",
        "store",
        "mac",
        "ipad",
        "iphone",
        "watch",
        "airpods",
        "tv & home",
        "entertainment",
        "accessories",
        "support",
        "shop",
        "learn more",
        "buy",
        "compare",
        "search",
        "bag",
    }
    if lower in blocked_exact:
        return False

    if lower in {clean_text(name).lower(), clean_text(description).lower()}:
        return False

    word_count = len(re.findall(r"\w+", text, flags=re.UNICODE))
    has_fact_marker = ":" in text or bool(re.search(r"\d", text))
    if word_count < 4 and not has_fact_marker:
        return False

    if len(text) < 18 or len(text) > 140:
        return False

    nav_patterns = (
        r"^(accueil|home|menu|produits|services|contact|about|login|cart|wishlist)$",
        r"^(acheter|voir|explorer|decouvrir|découvrir)\s",
    )
    return not any(re.search(pattern, lower, flags=re.I) for pattern in nav_patterns)


def build_features(name: str, description: str, text_chunks: List[str], product_json: Dict[str, Any]) -> List[str]:
    features: List[str] = []

    brand = product_json.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    if brand:
        features.append(f"Marque : {clean_text(brand)}")

    sku = product_json.get("sku") or product_json.get("mpn")
    if sku:
        features.append(f"Référence : {clean_text(sku)}")

    for chunk in text_chunks:
        if is_useful_feature(chunk, name, description):
            features.append(chunk)
        if len(features) >= 6:
            break

    if len(features) < 4:
        defaults = [
            "Informations produit récupérées automatiquement",
            "Image et description prêtes pour une landing page",
            "Contenu adapté au template choisi",
            "Bouton d'achat lié à l'URL originale",
        ]
        features = [*features, *default_marketing_features(name)]

    return unique(features)[:6]


def extract_metadata(html: str, base_url: str, requested_name: str = "") -> Dict[str, Any]:
    parser = ProductMetadataParser()
    parser.feed(html)

    json_ld_items = parse_json_ld_blocks(parser.json_ld_blocks)
    product_json = find_product_json_ld(json_ld_items)
    images = extract_images(html, base_url)

    # Extract all images from JSON-LD
    json_image = product_json.get("image", "")
    json_images: List[str] = []
    if isinstance(json_image, list):
        for entry in json_image:
            if isinstance(entry, dict):
                entry = entry.get("url", "")
            url = normalize_image_url(str(entry), base_url)
            if url:
                json_images.append(url)
    elif isinstance(json_image, dict):
        url = normalize_image_url(str(json_image.get("url", "")), base_url)
        if url:
            json_images.append(url)
    elif json_image:
        url = normalize_image_url(str(json_image), base_url)
        if url:
            json_images.append(url)

    title = clean_text(product_json.get("name") or parser.title or parser.meta.get("og:title") or "Produit")
    description = clean_text(product_json.get("description") or parser.description or parser.meta.get("og:description"))
    category = clean_text(
        product_json.get("category")
        or parser.meta.get("product:category")
        or parser.meta.get("article:section")
        or parser.meta.get("og:type")
        or ""
    )
    brand = extract_brand(product_json, parser)
    platform = detect_ecommerce_platform(base_url, html)

    # Extract all prices from multiple sources
    json_price = extract_price_from_json_ld(product_json)
    html_price = parse_price(html)
    parser_prices = unique(parser.prices)
    all_prices = unique([json_price, html_price, *parser_prices])
    price = next((p for p in all_prices if p), "")

    parser_image = normalize_image_url(parser.image, base_url)
    primary_image = json_images[0] if json_images else parser_image
    image_product_name = clean_text(requested_name) or title
    images = filter_product_images(unique([*json_images, *images, parser_image]), image_product_name, primary_image)
    image = images[0] if images else select_best_image(images, parser_image)

    # Extract all availability information
    json_availability = extract_availability_from_json_ld(product_json)
    parser_availability = unique(parser.availability)
    availability = json_availability or (parser_availability[0] if parser_availability else "")

    # Extract all rating information
    json_rating = extract_rating(product_json)
    parser_ratings = unique(parser.ratings)
    rating = json_rating or (parser_ratings[0] if parser_ratings else "")

    # Build comprehensive features
    features = build_features(title, description, parser.text_chunks, product_json)
    technical_specs = extract_technical_specs(product_json, html, parser.text_chunks)
    reviews = extract_reviews(product_json, parser)

    return {
        "title": title,
        "description": description or f"{title} est prêt à être présenté dans une landing page mono-produit.",
        "price": price,
        "brand": brand,
        "category": category,
        "technicalSpecs": technical_specs,
        "reviews": reviews,
        "platform": platform,
        "sourceType": platform,
        "allPrices": all_prices,
        "image": image,
        "images": images,
        "availability": availability,
        "allAvailability": parser_availability,
        "rating": rating,
        "allRatings": parser_ratings,
        "features": features,
        "rawTextChunks": parser.text_chunks[:20],
    }


def make_marketing_product(name: str, url: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    product_name = clean_text(name) or metadata["title"]
    description = clean_text(metadata.get("description"))
    tagline_source = description or metadata["title"]
    tagline = tagline_source.split(".")[0][:110].strip()
    if len(tagline) < 12:
        tagline = f"Découvrez {product_name} avec une page claire, moderne et prête à vendre."

    return {
        "name": product_name,
        "tagline": tagline,
        "description": description,
        "price": metadata.get("price") or "Prix disponible sur la page produit",
        "brand": metadata.get("brand") or "",
        "category": metadata.get("category") or "Produit e-commerce",
        "technicalSpecs": metadata.get("technicalSpecs") or [],
        "reviews": metadata.get("reviews") or [],
        "image": metadata.get("image") or DEFAULT_IMAGE,
        "images": metadata.get("images") or [],
        "features": metadata.get("features") or [],
        "benefits": [
            "Une presentation claire qui met le produit au centre",
            "Des preuves utiles extraites depuis la page source",
            "Un parcours court entre decouverte et achat",
        ],
        "faq": [
            {"question": "Ou acheter ce produit ?", "answer": "Le bouton principal renvoie vers la page produit originale."},
            {"question": "Les informations sont-elles verifiees ?", "answer": "Elles sont extraites depuis la source et enrichies sans inventer les donnees critiques."},
        ],
        "ctaTexts": ["Voir le produit", "Comparer les details", "Acheter maintenant"],
        "cta": "Voir le produit",
        "url": url,
        "availability": metadata.get("availability") or "Disponibilité à vérifier",
        "rating": metadata.get("rating") or "",
        "sourceType": metadata.get("sourceType") or metadata.get("platform") or "url",
        "sections": {
            "benefitTitle": f"Pourquoi choisir {product_name} ?",
            "proofTitle": "Informations extraites depuis la page produit",
            "closingTitle": f"Passez à {product_name}",
        },
    }


def validate_product_request(name: str, url: str) -> None:
    if not name:
        raise HTTPException(status_code=400, detail="Nom du produit requis.")
    if not url:
        raise HTTPException(status_code=400, detail="URL de produit requise.")
    if url.startswith("image-upload://"):
        return
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="L'URL doit commencer par http:// ou https://.")

    parsed_url = urlparse(url)
    if not parsed_url.netloc:
        raise HTTPException(status_code=400, detail="URL de produit invalide.")


def validate_product_match(name: str, url: str, metadata: Dict[str, Any]) -> None:
    tokens = strong_image_tokens(name)
    if not tokens:
        return

    searchable_text = " ".join(
        [
            url,
            metadata.get("title", ""),
            metadata.get("description", ""),
            " ".join(str(feature) for feature in metadata.get("features", [])[:4]),
        ]
    ).lower()
    compact_text = re.sub(r"[^a-z0-9]+", "", searchable_text)
    if not any(token in searchable_text or token in compact_text for token in tokens):
        raise HTTPException(
            status_code=400,
            detail="Le nom du produit ne correspond pas à l'URL fournie. Vérifiez le nom ou collez l'URL exacte du produit.",
        )


def calculate_product_similarity(expected_name: str, metadata: Dict[str, Any], candidate_url: str = "") -> float:
    expected_tokens = set(strong_image_tokens(expected_name) or image_tokens(expected_name))
    if not expected_tokens:
        return 70.0

    candidate_text = " ".join(
        [
            candidate_url,
            metadata.get("title", ""),
            metadata.get("name", ""),
            metadata.get("description", ""),
            metadata.get("category", ""),
            " ".join(str(feature) for feature in metadata.get("features", [])[:6]),
        ]
    ).lower()
    compact = re.sub(r"[^a-z0-9]+", "", candidate_text)

    matches = 0
    for token in expected_tokens:
        if token in candidate_text or token in compact:
            matches += 1

    score = (matches / max(len(expected_tokens), 1)) * 100
    title = clean_text(metadata.get("title") or metadata.get("name", "")).lower()
    if title and any(token in title for token in expected_tokens):
        score += 12

    expected = set(image_tokens(expected_name))
    competing_terms = {
        "airpods",
        "case",
        "cover",
        "galaxy",
        "ipad",
        "iphone",
        "macbook",
        "pixel",
        "samsung",
        "watch",
    }
    for term in competing_terms - expected:
        if term in compact:
            score -= 28

    return max(0.0, min(100.0, round(score, 2)))


def verify_image_product_match(
    image_base64: str,
    media_type: str,
    detected_name: str,
    scraped_product: Dict[str, Any],
    threshold: float = 65.0,
) -> Dict[str, Any]:
    """Verify that a scraped result still describes the uploaded image product.
    Uses local token matching and optionally Claude Vision for visual verification.
    Threshold increased from 55% to 65% to reduce false positives on accessories/cases."""
    
    # First, do a strict local check
    local_score = calculate_product_similarity(detected_name, scraped_product, scraped_product.get("url", ""))
    
    # Additional local check: reject if scraped product mentions competing/accessory terms
    scraped_title = (scraped_product.get("title") or scraped_product.get("name") or "").lower()
    scraped_desc = (scraped_product.get("description") or "").lower()
    scraped_text = scraped_title + " " + scraped_desc
    expected_name_lower = detected_name.lower()
    expected_tokens = set(image_tokens(detected_name))
    
    # Check for accessory terms that would indicate a different product
    accessory_terms = {"case", "cover", "screen", "protector", "charger", "cable", "skin", "sticker", 
                       "accessory", "bundle", "holster", "pouch", "sleeve", "stand", "mount", "holder",
                       "adapter", "converter", "strap", "band", "filter", "lens", "tripod", "grip", 
                       "dock", "pad", "mat", "cleaner", "kit", "tempered", "glass", "film", "guard"}
    
    # Only penalize accessories if the product name itself isn't an accessory
    is_accessory_product = any(term in expected_name_lower for term in ["case", "cover", "protector", "cable", "strap"])
    if not is_accessory_product:
        if any(term in scraped_title for term in accessory_terms):
            print(f"[verify_image_product_match] REJECTED: scraped product mentions accessory term in title: {scraped_title}")
            return {
                "accepted": False,
                "similarityScore": min(local_score, 30.0),
                "reason": "Scraped product appears to be an accessory, case, or cover, not the actual product",
                "method": "local_strict",
                "localScore": local_score,
            }
    
    # Check for competing product brands
    competing_brands = {"iphone", "ipad", "macbook", "imac", "airpods", "apple watch", "galaxy", "pixel", "xbox", "playstation", "airpods pro", "airpods max"}
    expected_brand = None
    for brand in competing_brands:
        if brand in expected_name_lower:
            expected_brand = brand
            break
    
    if expected_brand:
        # If the scraped product has a different brand name, reject it
        scraped_brand = None
        for brand in competing_brands:
            if brand in scraped_text and brand != expected_brand:
                scraped_brand = brand
                break
        if scraped_brand:
            print(f"[verify_image_product_match] REJECTED: expected '{expected_brand}' but scraped mentions '{scraped_brand}'")
            return {
                "accepted": False,
                "similarityScore": 15.0,
                "reason": f"The scraped product is a different product (found '{scraped_brand}' instead of '{expected_brand}')",
                "method": "local_strict",
                "localScore": local_score,
            }
    
    # Competing product detection: if the URL or title has terms for completely different products
    url_lower = (scraped_product.get("url") or "").lower()
    url_compact = re.sub(r"[^a-z0-9]+", "", url_lower)
    expected_compact = re.sub(r"[^a-z0-9]+", "", expected_name_lower)
    
    # If product name contains only iPhone terms but scraped URL contains only Samsung/AirPods terms
    iphone_terms = {"iphone", "ipad", "apple", "ios"}
    samsung_terms = {"galaxy", "samsung", "android"}
    airpods_terms = {"airpods", "airpods pro", "airpods max"}
    
    expected_has_iphone = any(t in expected_compact for t in iphone_terms)
    expected_has_samsung = any(t in expected_compact for t in samsung_terms)
    expected_has_airpods = any(t in expected_compact for t in airpods_terms)
    
    scraped_has_iphone = any(t in url_compact or t in scraped_title for t in iphone_terms)
    scraped_has_samsung = any(t in url_compact or t in scraped_title for t in samsung_terms)
    scraped_has_airpods = any(t in url_compact or t in scraped_title for t in airpods_terms)
    
    if expected_has_iphone and (scraped_has_samsung or scraped_has_airpods):
        return {"accepted": False, "similarityScore": 10.0, "reason": "Product brand mismatch (Apple vs Samsung/AirPods)", "method": "local_strict", "localScore": local_score}
    if expected_has_samsung and (scraped_has_iphone or scraped_has_airpods):
        return {"accepted": False, "similarityScore": 10.0, "reason": "Product brand mismatch (Samsung vs Apple/AirPods)", "method": "local_strict", "localScore": local_score}
    if expected_has_airpods and (scraped_has_iphone or scraped_has_samsung):
        return {"accepted": False, "similarityScore": 10.0, "reason": "Product brand mismatch (AirPods vs other)", "method": "local_strict", "localScore": local_score}

    # Local threshold check - increased to 65%
    if local_score < threshold:
        print(f"[verify_image_product_match] Local score {local_score} below threshold {threshold}")
        return {
            "accepted": False,
            "similarityScore": local_score,
            "reason": "Product name tokens do not match the scraped data sufficiently",
            "method": "local",
            "localScore": local_score,
        }

    result = {
        "accepted": local_score >= threshold,
        "similarityScore": local_score,
        "reason": "Token similarity match",
        "method": "local",
    }

    # Try Claude for visual verification (when available)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key, timeout=20.0)
            prompt = {
                "detectedImageProduct": detected_name,
                "scrapedProduct": {
                    "name": scraped_product.get("title") or scraped_product.get("name"),
                    "description": scraped_product.get("description"),
                    "features": scraped_product.get("features", [])[:6],
                    "url": scraped_product.get("url"),
                },
                "rules": (
                    "CRITICAL: Return only JSON. Reject if the scraped product is an accessory (case, cover, screen protector, charger, cable, adapter, strap, band, skin, sticker). "
                    "Reject if it's a DIFFERENT MODEL or BRAND. Accept only exact same product, same model variants (different color/storage), or same product on different stores."
                ),
            }
            message = client.messages.create(
                model=DEFAULT_ANTHROPIC_MODEL,
                max_tokens=350,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": image_base64,
                                },
                            },
                            {
                                "type": "text",
                                "text": (
                                    "Compare this uploaded product image with the scraped product data. "
                                    "Return only JSON: {\"accepted\": boolean, \"similarityScore\": 0-100, \"reason\": \"short\"}. "
                                    f"Data: {json.dumps(prompt, ensure_ascii=False)}"
                                ),
                            },
                        ],
                    }
                ],
            )
            ai_data = extract_json_object(message.content[0].text.strip())
            if ai_data:
                ai_score = float(ai_data.get("similarityScore", local_score) or 0)
                return {
                    "accepted": bool(ai_data.get("accepted")) and ai_score >= threshold,
                    "similarityScore": max(0.0, min(100.0, round(ai_score, 2))),
                    "reason": clean_text(ai_data.get("reason", "")) or "AI visual verification",
                    "method": "claude",
                    "localScore": local_score,
                }
        except Exception as exc:
            print(f"[verify_image_product_match] Claude verification skipped: {exc}")

    return result


def extract_json_object(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}

    return data if isinstance(data, dict) else {}


def claude_text_from_response(response: Dict[str, Any]) -> str:
    content = response.get("content", [])
    if not isinstance(content, list):
        return ""

    parts: List[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(parts).strip()


def build_claude_prompt(base_product: Dict[str, Any], metadata: Dict[str, Any]) -> str:
    payload = {
        "product": {
            "name": base_product.get("name"),
            "url": base_product.get("url"),
            "price": base_product.get("price"),
            "availability": base_product.get("availability"),
            "rating": base_product.get("rating"),
            "description": metadata.get("description"),
            "features": metadata.get("features", []),
            "category": metadata.get("category"),
            "platform": metadata.get("platform"),
        }
    }
    return (
        "Tu es un copywriter e-commerce senior francophone. "
        "Genere du contenu marketing et SEO pour une landing page mono-produit. "
        "N'invente pas de prix, de disponibilite, de note ou de caracteristique technique non fournie. "
        "Retourne uniquement un objet JSON valide avec les cles suivantes: "
        "name, tagline, description, features, benefits, faq, cta, ctaTexts, seoTitle, seoDescription, sections. "
        "features doit contenir 4 a 6 avantages courts. "
        "benefits doit contenir 3 a 5 benefices orientes client. "
        "faq doit contenir 3 objets {question, answer}. "
        "ctaTexts doit contenir 2 a 4 libelles courts. "
        "sections doit contenir benefitTitle, proofTitle et closingTitle. "
        "Voici les donnees disponibles:\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def call_claude_marketing(base_product: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return {}

    model = os.environ.get("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL).strip() or DEFAULT_ANTHROPIC_MODEL
    try:
        max_tokens = int(os.environ.get("ANTHROPIC_MAX_TOKENS", "1200"))
    except ValueError:
        max_tokens = 1200
    request_body = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0.4,
        "messages": [
            {
                "role": "user",
                "content": build_claude_prompt(base_product, metadata),
            }
        ],
    }
    request = Request(
        ANTHROPIC_API_URL,
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="ignore")
            return extract_json_object(claude_text_from_response(json.loads(body)))
    except Exception:
        return {}


def merge_claude_product(base_product: Dict[str, Any], claude_data: Dict[str, Any]) -> Dict[str, Any]:
    if not claude_data:
        return {**base_product, "contentSource": "fallback"}

    product = {**base_product, "contentSource": "claude"}
    for key in ("name", "tagline", "description", "cta"):
        value = clean_text(claude_data.get(key))
        if value:
            product[key] = value

    features = claude_data.get("features")
    if isinstance(features, list):
        clean_features = [clean_text(feature) for feature in features if clean_text(feature)]
        if clean_features:
            product["features"] = clean_features[:6]

    benefits = claude_data.get("benefits")
    if isinstance(benefits, list):
        clean_benefits = [clean_text(benefit) for benefit in benefits if clean_text(benefit)]
        if clean_benefits:
            product["benefits"] = clean_benefits[:5]

    cta_texts = claude_data.get("ctaTexts")
    if isinstance(cta_texts, list):
        clean_ctas = [clean_text(cta) for cta in cta_texts if clean_text(cta)]
        if clean_ctas:
            product["ctaTexts"] = clean_ctas[:4]

    faq = claude_data.get("faq")
    if isinstance(faq, list):
        clean_faq = []
        for item in faq:
            if not isinstance(item, dict):
                continue
            question = clean_text(item.get("question"))
            answer = clean_text(item.get("answer"))
            if question and answer:
                clean_faq.append({"question": question, "answer": answer})
        if clean_faq:
            product["faq"] = clean_faq[:4]

    sections = claude_data.get("sections")
    if isinstance(sections, dict):
        product["sections"] = {
            **base_product.get("sections", {}),
            **{key: clean_text(value) for key, value in sections.items() if clean_text(value)},
        }

    seo_title = clean_text(claude_data.get("seoTitle"))
    seo_description = clean_text(claude_data.get("seoDescription"))
    product["seo"] = {
        "title": seo_title or product["name"],
        "description": seo_description or product["tagline"],
    }
    return product


def enrich_product_with_claude(base_product: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
    claude_data = call_claude_marketing(base_product, metadata)
    return merge_claude_product(base_product, claude_data)
# remember this line start here
SERPAPI_KEY = os.getenv("SERPAPI_KEY")

def fetch_google_images(product_name: str, count: int = 10) -> List[str]:
    """Search Google Images using SerpAPI to find product images."""
    if not SERPAPI_KEY:
        return []
    
    images: List[str] = []
    try:
        response = requests.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google_images",
                "q": product_name,
                "api_key": SERPAPI_KEY,
                "tbs": "imgo:1",
                "ijn": 0,
            },
            timeout=15,
        )
        data = response.json()
        
        for result in data.get("images_results", []):
            url = result.get("original") or result.get("thumbnail") or ""
            url = clean_text(url)
            if url and url.startswith(("http://", "https://")):
                images.append(url)
                if len(images) >= count:
                    break
        
        print(f"[fetch_google_images] Found {len(images)} images for '{product_name}'")
        return images
    except Exception as e:
        print(f"[fetch_google_images] Error: {e}")
        return []


def fetch_images_from_amazon(product_name: str, max_images: int = 5) -> List[str]:
    """Scrape Amazon search results for product images."""
    images: List[str] = []
    try:
        search_url = f"https://www.amazon.com/s?k={requests.utils.quote(product_name)}"
        search_html = fetch_page(search_url, timeout=10)
        
        # Extract image URLs from Amazon search results
        # Amazon uses data-old-hires and data-a-dynamic-image attributes
        img_patterns = [
            r'<img[^>]+src=["\']([^"\']+\.(?:jpg|jpeg|png|webp)(?:\?[^"\']*)?)["\'][^>]*>',
            r'data-old-hires=["\']([^"\']+)["\']',
            r'data-a-dynamic-image=["\']([^"\']+)["\']',
        ]
        
        base_url = "https://www.amazon.com"
        for pattern in img_patterns:
            for match in re.finditer(pattern, search_html, flags=re.I):
                raw = match.group(1)
                # data-a-dynamic-image contains JSON with URLs as keys
                if raw.startswith("{"):
                    try:
                        dynamic_data = json.loads(raw)
                        for img_url in dynamic_data.keys():
                            url = normalize_image_url(img_url, base_url)
                            if url and url not in images:
                                images.append(url)
                    except json.JSONDecodeError:
                        pass
                else:
                    url = normalize_image_url(raw.strip('"').strip("'"), base_url)
                    if url and url not in images:
                        images.append(url)
        
        images = images[:max_images]
        print(f"[fetch_images_from_amazon] Found {len(images)} images for '{product_name}'")
        return images
    except Exception as e:
        print(f"[fetch_images_from_amazon] Error: {e}")
        return []


def search_multiple_image_sources(
    product_name: str,
    existing_images: List[str] = None,
    reference_image_base64: str = "",
    reference_media_type: str = "image/jpeg",
) -> List[str]:
    """Aggregate images from multiple sources: Google Images, Amazon, and existing."""
    all_images: List[str] = []
    if existing_images:
        all_images.extend(existing_images)
    
    product_name_clean = clean_text(product_name)
    if not product_name_clean:
        return unique(all_images)
    
    # Search Google Images
    google_images = fetch_google_images(product_name_clean, count=10)
    all_images.extend(google_images)
    
    # Search Amazon
    amazon_images = fetch_images_from_amazon(product_name_clean, max_images=5)
    all_images.extend(amazon_images)
    
    # Deduplicate, remove banners/logos/competitors, and keep the highest-signal product photos.
    result = filter_product_images(unique(all_images), product_name_clean)
    if reference_image_base64:
        result = filter_images_by_visual_similarity(
            result,
            reference_image_base64,
            reference_media_type,
            product_name_clean,
            IMAGE_SIMILARITY_THRESHOLD,
        )
    print(f"[search_multiple_image_sources] Total unique images for '{product_name_clean}': {len(result)}")
    return result


def image_bytes_to_base64(image_data: bytes) -> str:
    return base64.standard_b64encode(image_data).decode("utf-8")


def guess_media_type(url: str, fallback: str = "image/jpeg") -> str:
    lower = urlparse(url).path.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    return fallback


def compare_images_with_openai(
    reference_base64: str,
    reference_media_type: str,
    candidate_base64: str,
    candidate_media_type: str,
    product_name: str,
) -> Dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {}
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=DEFAULT_OPENAI_VISION_MODEL,
            response_format={"type": "json_object"},
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Compare ces deux images e-commerce. Reponds uniquement en JSON: "
                                "{\"accepted\": boolean, \"similarityScore\": 0-100, \"reason\": \"court\", "
                                "\"viewType\": \"front|back|side|marketing|usage|unknown\"}. "
                                "Accepte uniquement le meme produit exact ou une variante directe "
                                "(couleur, capacité, pack officiel). Rejette logos, pubs, accessoires, "
                                f"captures non pertinentes et produits differents. Produit attendu: {product_name}."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{reference_media_type};base64,{reference_base64}"},
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{candidate_media_type};base64,{candidate_base64}"},
                        },
                    ],
                }
            ],
        )
        return extract_json_object(response.choices[0].message.content or "")
    except Exception as exc:
        print(f"[compare_images_with_openai] skipped: {exc}")
        return {}


def compare_images_with_claude(
    reference_base64: str,
    reference_media_type: str,
    candidate_base64: str,
    candidate_media_type: str,
    product_name: str,
) -> Dict[str, Any]:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return {}
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key, timeout=20.0)
        message = client.messages.create(
            model=DEFAULT_ANTHROPIC_MODEL,
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": reference_media_type, "data": reference_base64}},
                        {"type": "image", "source": {"type": "base64", "media_type": candidate_media_type, "data": candidate_base64}},
                        {
                            "type": "text",
                            "text": (
                                "Compare ces deux images produit. Reponds uniquement avec JSON valide: "
                                "{\"accepted\": boolean, \"similarityScore\": 0-100, \"reason\": \"court\", "
                                "\"viewType\": \"front|back|side|marketing|usage|unknown\"}. "
                                "Accepte seulement le meme produit ou une variante directe. Rejette logos, publicites, "
                                f"accessoires et produits differents. Produit attendu: {product_name}."
                            ),
                        },
                    ],
                }
            ],
        )
        return extract_json_object(message.content[0].text.strip())
    except Exception as exc:
        print(f"[compare_images_with_claude] skipped: {exc}")
        return {}


def verify_candidate_image_similarity(
    reference_base64: str,
    reference_media_type: str,
    candidate_url: str,
    product_name: str,
    threshold: float = IMAGE_SIMILARITY_THRESHOLD,
) -> Dict[str, Any]:
    candidate_bytes = fetch_image_bytes(candidate_url, timeout=12)
    if not candidate_bytes or len(candidate_bytes) < 500:
        return {"accepted": False, "similarityScore": 0, "reason": "Image candidate introuvable", "method": "download"}

    candidate_base64 = image_bytes_to_base64(candidate_bytes[:4 * 1024 * 1024])
    candidate_media_type = guess_media_type(candidate_url)
    result = compare_images_with_openai(
        reference_base64, reference_media_type, candidate_base64, candidate_media_type, product_name
    ) or compare_images_with_claude(
        reference_base64, reference_media_type, candidate_base64, candidate_media_type, product_name
    )

    if result:
        score = float(result.get("similarityScore", 0) or 0)
        return {
            "accepted": bool(result.get("accepted")) and score >= threshold,
            "similarityScore": max(0.0, min(100.0, round(score, 2))),
            "reason": clean_text(result.get("reason", "")),
            "viewType": clean_text(result.get("viewType", "unknown")),
            "method": "vision",
        }

    local_score = image_relevance_score(candidate_url, product_name)
    heuristic_score = max(0.0, min(79.0, float(local_score * 7)))
    return {
        "accepted": False,
        "similarityScore": heuristic_score,
        "reason": "Validation IA indisponible: image non sauvegardee car le seuil visuel de 80% ne peut pas etre garanti",
        "viewType": "unknown",
        "method": "local_heuristic",
    }


def filter_images_by_visual_similarity(
    images: List[str],
    reference_base64: str,
    reference_media_type: str,
    product_name: str,
    threshold: float = IMAGE_SIMILARITY_THRESHOLD,
) -> List[str]:
    if not reference_base64:
        return images[:5]

    accepted: List[str] = []
    rejected = 0
    for image_url in unique(images)[:12]:
        verification = verify_candidate_image_similarity(
            reference_base64,
            reference_media_type,
            image_url,
            product_name,
            threshold,
        )
        if verification["accepted"]:
            accepted.append(image_url)
        else:
            rejected += 1
        if len(accepted) >= 6:
            break

    print(f"[filter_images_by_visual_similarity] accepted={len(accepted)} rejected={rejected} product='{product_name}'")
    return accepted

def search_product_on_google(product_name: str):
    try:
        # More specific query: exclude accessories/cases/covers and search for exact product
        query = (
            f'"{product_name}" buy OR price OR official '
            "(Amazon OR Shopify OR store OR site) "
            "-case -cover -screen -protector -charger -cable -skin -sticker -accessory -bundle"
        )
        response = requests.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google",
                "q": query,
                "api_key": SERPAPI_KEY,
            },
            timeout=10,
        )

        data = response.json()

        if data.get("organic_results"):
            preferred_domains = (
                "amazon.",
                "etsy.",
                "aliexpress.",
                "shopify",
                "myshopify.",
                "woocommerce",
            )
            results = data["organic_results"][:8]
            
            # First pass: look for exact product name match in title
            name_lower = product_name.lower()
            name_tokens = set(image_tokens(product_name))
            
            for result in results:
                link = result.get("link", "")
                title = result.get("title", "").lower()
                snippet = result.get("snippet", "").lower()
                
                # Check if the result title/snippet contains the exact product name
                title_has_name = name_lower in title or name_lower in snippet
                title_tokens = set(image_tokens(title + " " + snippet))
                token_overlap = len(name_tokens & title_tokens)
                
                # Only accept if there's significant token overlap
                if token_overlap < max(2, len(name_tokens) // 2):
                    continue
                    
                # Check for competing/accessory terms in title
                accessory_terms = {"case", "cover", "screen", "protector", "charger", "cable", "skin", "sticker", "accessory", "bundle", "holster", "pouch", "sleeve", "stand", "mount", "holder", "adapter", "converter", "strap", "band", "tip", "filter", "lens", "tripod", "grip", "dock", "pad", "mat", "cleaner", "kit"}
                title_has_accessory = any(term in title for term in accessory_terms)
                if title_has_accessory:
                    continue
                
                if any(domain in link.lower() for domain in preferred_domains):
                    return link
            
            # Second pass: less strict but still check for accessory terms
            for result in results:
                link = result.get("link", "")
                title = result.get("title", "").lower()
                
                title_has_accessory = any(term in title for term in {"case", "cover", "screen", "protector", "charger", "cable", "accessory"})
                if title_has_accessory:
                    continue
                    
                if any(domain in link.lower() for domain in preferred_domains):
                    return link
            
            # Last resort: return first result that doesn't mention accessories
            for result in results:
                link = result.get("link", "")
                title = result.get("title", "").lower()
                if not any(term in title for term in {"case", "cover", "screen", "protector", "charger", "cable", "accessory"}):
                    return link

        return None

    except Exception as e:
        print("Google search error:", e)
        return None


def is_generic_product_name(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", clean_text(value).lower()).strip()
    return not normalized or normalized in {
        "produit",
        "produit detecte",
        "produit détecté",
        "product",
        "detected product",
    }


def infer_product_name_from_search_text(title: str, snippet: str = "") -> str:
    text = clean_text(title) or clean_text(snippet)
    if not text:
        return ""

    text = re.sub(r"\s+", " ", text)
    text = re.split(r"\s(?:\||–|-)\s", text, maxsplit=1)[0]
    text = re.sub(r"^(buy|price|official|shop|acheter|prix)\s+", "", text, flags=re.I)
    text = re.sub(r"\s+(amazon|etsy|aliexpress|shopify|official store).*$", "", text, flags=re.I)
    text = clean_text(text.strip(" :-|"))
    if len(text) < 4:
        return ""
    return text[:120]


def extract_price_from_search_text(*parts: str) -> str:
    text = " ".join(clean_text(part) for part in parts if clean_text(part))
    match = re.search(
        r"(?:(?:\$|€|£)\s?\d+(?:[,.]\d{1,2})?|\d+(?:[,.]\d{1,2})?\s?(?:€|eur|mad|dh|usd|\$|£))",
        text,
        flags=re.I,
    )
    return clean_text(match.group(0)) if match else ""


def search_product_by_image_google_result(image_base64: str, product_name: str = "") -> Dict[str, str]:
    """Search Google reverse image via SerpAPI and return the best product URL plus an inferred name."""
    try:
        product_tokens = set(image_tokens(product_name))
        has_product_name = not is_generic_product_name(product_name)
        accessory_terms = {"case", "cover", "screen", "protector", "charger", "cable", "skin", "sticker", 
                           "accessory", "bundle", "holster", "pouch", "sleeve", "stand", "mount", "holder",
                           "adapter", "converter", "strap", "band", "filter", "lens", "tripod", "grip", 
                           "dock", "pad", "mat", "cleaner", "kit", "tempered", "glass"}
        preferred_domains = (
            "amazon.",
            "etsy.",
            "aliexpress.",
            "shopify",
            "myshopify.",
            "woocommerce",
            "msi.",
            "bestbuy.",
            "walmart.",
            "newegg.",
        )
        params = {
            "engine": "google_reverse_image",
            "image_base64": image_base64,
            "api_key": SERPAPI_KEY,
        }
        if has_product_name:
            params["q"] = product_name

        response = requests.get(
            "https://serpapi.com/search.json",
            params=params,
            timeout=15,
        )

        data = response.json()
        best: Dict[str, str] = {}
        best_score = -100

        # Try organic results first - filter by product relevance
        if data.get("organic_results"):
            for result in data["organic_results"][:10]:
                link = result.get("link", "")
                raw_title = result.get("title", "") or ""
                raw_snippet = result.get("snippet", "") or ""
                title = raw_title.lower()
                snippet = raw_snippet.lower()
                
                if not link or not any(ext in link for ext in ['.com', '.fr', '.ma', '.net', '.org']):
                    continue
                
                # Check title has significant product overlap
                title_tokens = set(image_tokens(title + " " + snippet))
                overlap = len(product_tokens & title_tokens)
                
                # Check for accessory terms in title
                has_accessory = any(t in title for t in accessory_terms)
                
                score = overlap
                if has_accessory:
                    score -= 10
                if has_product_name and product_name.lower() in title:
                    score += 5
                if any(domain in link.lower() for domain in preferred_domains):
                    score += 3
                if any(word in title for word in ("price", "buy", "shop", "store", "official", "amazon", "pc", "gaming")):
                    score += 1
                
                if score > best_score:
                    best_score = score
                    best = {
                        "url": link,
                        "name": infer_product_name_from_search_text(raw_title, raw_snippet),
                        "price": extract_price_from_search_text(raw_title, raw_snippet),
                    }
            
            # With a real typed name, require token overlap. Without it, accept the best non-accessory result.
            if best and (best_score >= 2 or (not has_product_name and best_score >= 0)):
                return best

        # Fallback: try inline images with less strict filtering
        if data.get("inline_images"):
            seen_links = set()
            for result in data["inline_images"][:10]:
                link = result.get("link", "") or result.get("source", "")
                raw_title = result.get("title", "") or ""
                title = raw_title.lower()
                
                if not link or link in seen_links:
                    continue
                seen_links.add(link)
                
                # Skip accessory results
                if has_product_name and product_name.lower() not in title and any(t in title for t in {"case", "cover", "protector", "cable"}):
                    continue
                if not has_product_name and any(t in title for t in {"case", "cover", "protector", "cable"}):
                    continue
                    
                if link:
                    return {
                        "url": link,
                        "name": infer_product_name_from_search_text(raw_title),
                        "price": extract_price_from_search_text(raw_title),
                    }

        return {}

    except Exception as e:
        print("Google reverse image search error:", e)
        return {}


def search_product_by_image_google(image_base64: str, product_name: str = "") -> str:
    return search_product_by_image_google_result(image_base64, product_name).get("url", "")

# end

@app.post("/api/product/{product_id}/images/more")
def fetch_more_product_images(product_id: int, payload: Dict[str, Any] = None) -> Dict[str, Any]:
    """Fetch additional images from multiple sources for an existing product."""
    try:
        product_name = clean_text(payload.get("name", "")) if payload else ""
        existing_images = payload.get("existing_images", []) if payload else []
        
        if not product_name:
            raise HTTPException(status_code=400, detail="Nom du produit requis.")
        
        additional_images = search_multiple_image_sources(product_name, existing_images)
        
        # Deduplicate with existing
        from itertools import chain
        merged = list(dict.fromkeys(chain(existing_images, additional_images)))
        
        return {
            "status": "ok",
            "images": merged,
            "total": len(merged),
            "new_images": len(additional_images),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/")
def home() -> Dict[str, str]:
    return {"message": "Backend works"}


@app.get("/api/products")
def get_products() -> Dict[str, Any]:
    return {
        "status": "ok",
        "products": list_saved_products(),
    }


@app.post("/api/database/init")
def create_database_schema() -> Dict[str, str]:
    init_database()
    return {
        "status": "ok",
        "message": f"Base de donnees {DB_NAME} prete sur MySQL localhost.",
    }


@app.post("/api/auth/register")
def auth_register(payload: Dict[str, str]) -> Dict[str, Any]:
    user = register_user(
        payload.get("nom", ""),
        payload.get("email", ""),
        payload.get("motDePasse", ""),
    )
    return {
        "status": "ok",
        "user": user,
    }


@app.post("/api/auth/login")
def auth_login(payload: Dict[str, str]) -> Dict[str, Any]:
    user = authenticate_user(payload.get("email", ""), payload.get("motDePasse", ""))
    return {
        "status": "ok",
        "user": user,
    }


@app.post("/api/scrape")
def scrape_product(product: Dict[str, Any]) -> Dict[str, Any]:
    try:
        utilisateur_id = require_existing_user_id(product.get("utilisateurId"))
        name = clean_text(product.get("name", ""))
        url = clean_text(product.get("url", ""))

        validate_product_request(name, url)

        html = fetch_page(url)
        metadata = extract_metadata(html, url, name)
        validate_product_match(name, url, metadata)
        
        # Use only images extracted from the actual product URL (no external image search)
        # External image sources (Google Images, Amazon) often return unrelated results
        scraped_images = metadata.get("images", [])
        base_product = make_marketing_product(name, url, metadata)
        base_product["images"] = scraped_images
        if scraped_images:
            base_product["image"] = scraped_images[0]
        
        generated_product = enrich_product_with_claude(base_product, metadata)
        generated_product["images"] = scraped_images
        if scraped_images:
            generated_product["image"] = scraped_images[0]
        
        product_id = save_product_to_database(generated_product, utilisateur_id)
        generated_product["id"] = product_id

        return {
            "status": "ok",
            "database": {
                "saved": True,
                "productId": product_id,
            },
            "product": generated_product,
            "metadata": metadata,
            "multi_source_images": False,
        }
    except HTTPException:
        raise
    except Exception as exc:
        import traceback
        error_trace = traceback.format_exc()
        print(f"[scrape_product ERROR] {error_trace}")
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors du scraping du produit : {str(exc)}"
        )


@app.post("/api/product/search-by-image-url")
async def search_product_by_image_url(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Search for a product by passing an image URL. Downloads the image,
    performs reverse image search via Google/SerpAPI, finds the product URL,
    and scrapes it for full product information."""
    try:
        utilisateur_id = require_existing_user_id(payload.get("utilisateurId"))
        image_url = clean_text(payload.get("imageUrl", ""))
        product_name = clean_text(payload.get("name", ""))

        if not image_url:
            raise HTTPException(status_code=400, detail="URL de l'image requise.")
        if not image_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="URL d'image invalide.")

        # Download the image
        print(f"[search-by-image-url] Downloading image from: {image_url}")
        image_data = fetch_image_bytes(image_url)
        if not image_data:
            raise HTTPException(status_code=400, detail="Impossible de télécharger l'image depuis l'URL fournie.")

        # Convert to base64 for reverse image search
        import base64 as b64_module
        image_base64 = b64_module.standard_b64encode(image_data).decode("utf-8")
        image_hash = hashlib.sha256(image_data).hexdigest()[:16]
        database_url = f"image-upload://{image_hash}"
        content_type = "image/jpeg"
        try:
            import imghdr
            detected_type = imghdr.what(None, h=image_data)
            if detected_type:
                content_type = f"image/{detected_type}"
        except Exception:
            pass

        # Step 1: Try reverse image search to find the product URL
        found_url = None
        search_query = product_name or ""

        if SERPAPI_KEY:
            if search_query:
                found_url = search_product_by_image_google(image_base64, search_query)
            # Fallback: text search
            if not found_url and search_query:
                found_url = search_product_on_google(search_query)

        # Step 2: If we found a real URL, scrape it
        if found_url and found_url.startswith(("http://", "https://")):
            print(f"[search-by-image-url] Found product URL: {found_url}")
            try:
                html = fetch_page(found_url)
                scrape_metadata = extract_metadata(html, found_url, product_name)
                scrape_metadata["url"] = found_url
                verification = verify_image_product_match(
                    image_base64,
                    content_type,
                    product_name or scrape_metadata.get("title", ""),
                    scrape_metadata,
                )
                if not verification["accepted"]:
                    print(f"[search-by-image-url] Rejected candidate {found_url}: {verification}")
                    found_url = None
                    raise ValueError("Candidate product did not match source image")
                final_name = scrape_metadata.get("title") or product_name or "Produit détecté"
                base_product = make_marketing_product(final_name, found_url, scrape_metadata)
                base_product["similarityScore"] = verification["similarityScore"]
                base_product["verification"] = verification
                generated_product = enrich_product_with_claude(base_product, scrape_metadata)
                generated_product["similarityScore"] = verification["similarityScore"]
                generated_product["verification"] = verification

                if generated_product.get("image") == DEFAULT_IMAGE or not generated_product.get("image"):
                    scraped_images = filter_images_by_visual_similarity(
                        scrape_metadata.get("images", []),
                        image_base64,
                        content_type,
                        final_name,
                        IMAGE_SIMILARITY_THRESHOLD,
                    )
                    if scraped_images:
                        generated_product["image"] = scraped_images[0]
                    generated_product["images"] = scraped_images

                product_id = save_product_to_database(generated_product, utilisateur_id)
                generated_product["id"] = product_id

                return {
                    "status": "ok",
                    "database": {"saved": True, "productId": product_id},
                    "product": generated_product,
                    "metadata": scrape_metadata,
                    "search_source": "scraped_from_found_url",
                    "found_url": found_url,
                    "source_image_url": image_url,
                    "verification": verification,
                }
            except HTTPException:
                raise
            except Exception as scrape_error:
                print(f"[search-by-image-url] Scrape error for {found_url}: {scrape_error}")

        # Step 3: Fallback - use Claude vision to analyze image
        print(f"[search-by-image-url] No URL found, using image analysis")
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if api_key:
            try:
                content_type = "image/jpeg"
                import imghdr
                detected_type = imghdr.what(None, h=image_data)
                if detected_type:
                    content_type = f"image/{detected_type}"
            except Exception:
                pass

            metadata = extract_product_from_image(image_base64, content_type, product_name)
        else:
            metadata = local_image_product_metadata(product_name)

        detected_name = metadata.get("name") or product_name or "Produit détecté"

        # Search for additional images
        additional_images = search_multiple_image_sources(
            detected_name,
            metadata.get("images", []),
            image_base64,
            content_type,
        )

        fallback_name = detected_name if not is_generic_product_name(detected_name) else "Produit a identifier"
        fallback_tagline = metadata.get("tagline") if not is_generic_product_name(detected_name) else "Informations produit a completer"
        fallback_description = metadata.get("description") if not is_generic_product_name(detected_name) else ""
        base_product = {
            "name": detected_name,
            "tagline": metadata.get("tagline") or "Produit analysé depuis une image",
            "description": fallback_description or "",
            "name": fallback_name,
            "tagline": fallback_tagline,
            "price": metadata.get("price") or "Prix non détecté",
            "image": image_url,
            "images": [image_url, *additional_images] if image_url else additional_images,
            "brand": metadata.get("brand") or "",
            "features": metadata.get("features") or [],
            "technicalSpecs": metadata.get("technicalSpecs") or [],
            "reviews": metadata.get("reviews") or [],
            "cta": "Découvrir le produit",
            "url": database_url,
            "availability": metadata.get("availability") or "Vérifier la disponibilité",
            "rating": metadata.get("rating") or "",
            "category": metadata.get("category") or "Produit detecte par image",
            "sourceType": "image",
            "sections": {
                "benefitTitle": f"Pourquoi choisir ce produit ?",
                "proofTitle": "Informations détectées depuis l'image",
                "closingTitle": "Passez à l'action",
            },
        }

        generated_product = enrich_product_with_claude(base_product, metadata)
        generated_product["image"] = image_url
        generated_product["images"] = [image_url, *additional_images]

        database_product = {**generated_product, "url": database_url}
        product_id = save_product_to_database(database_product, utilisateur_id)
        generated_product["id"] = product_id
        generated_product["url"] = "#"

        return {
            "status": "ok",
            "database": {"saved": True, "productId": product_id},
            "product": generated_product,
            "metadata": metadata,
            "search_source": "image_analysis_only",
            "found_url": None,
            "source_image_url": image_url,
        }

    except HTTPException:
        raise
    except Exception as exc:
        import traceback
        error_trace = traceback.format_exc()
        print(f"[search-by-image-url ERROR] {error_trace}")
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors de la recherche par image : {str(exc)}"
        )


def fetch_image_bytes(url: str, timeout: int = 15) -> bytes:
    """Download an image from a URL and return its bytes."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
    }
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception as exc:
        print(f"[fetch_image_bytes] Error downloading {url}: {exc}")
        return b""


def save_uploaded_product_image(image_data: bytes, content_type: str, image_hash: str) -> str:
    extension = ".jpg"
    if "png" in content_type:
        extension = ".png"
    elif "webp" in content_type:
        extension = ".webp"
    elif "gif" in content_type:
        extension = ".gif"

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{image_hash}{extension}"
    target = UPLOAD_DIR / filename
    if not target.exists():
        target.write_bytes(image_data)
    return f"{PUBLIC_BASE_URL}/uploads/product_images/{filename}"


@app.post("/api/validate-image")
async def validate_product_image(
    file: UploadFile = File(...),
    name: str = Form(""),
) -> Dict[str, Any]:
    """Validate if an uploaded image corresponds to a real product using Claude Vision."""
    try:
        image_data = await file.read()
        if not image_data:
            return {"isValid": False, "message": "Aucune image fournie."}
        
        if len(image_data) > 5 * 1024 * 1024:
            return {"isValid": False, "message": "Image trop grande (max 5MB)."}
        
        content_type = file.content_type or ""
        if not content_type.startswith("image/"):
            return {"isValid": False, "message": "Fichier invalide. Veuillez télécharger une image."}
        
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            return {"isValid": True, "message": "", "note": "Clé API manquante, validation ignorée."}
        
        image_base64 = base64.standard_b64encode(image_data).decode("utf-8")
        media_type = content_type if content_type else "image/jpeg"
        
        import anthropic
        client = anthropic.Anthropic(api_key=api_key, timeout=15.0)
        
        product_name_context = f" Le nom fourni est: '{name}'." if name else ""
        
        prompt = f"""Tu es un expert en analyse d'images e-commerce. Analyse cette image et réponds UNIQUEMENT par un objet JSON valide (sans markdown, sans explications).

Réponds au format: {{"isProduct": true/false, "confidence": 0-100, "reason": "courte raison en français"}}

Critères pour qu'une image soit considérée comme un PRODUIT:
- L'image montre un objet commercialisable (électronique, vêtement, accessoire, meuble, cosmétique, jouet, outil, aliment emballé, etc.)
- L'image est une photo produit, une photo catalogue, ou une image promotionnelle d'un article
- Le produit est clairement visible (même s'il y a un fond simple ou un décor minimal)

N'est PAS un produit:
- Paysages, photos de nature, ciel, plages, montagnes
- Photos de personnes uniquement (selfies, portraits)
- Photos de nourriture non emballée (plat cuisiné, assiette au restaurant)
- Animaux, photos d'animaux domestiques
- Arts abstraits, galeries d'art
- Textes, documents, captures d'écran de code
- Photos floues, de très mauvaise qualité ou illisibles
- Images génériques, icônes, logos sans produit tangible
{product_name_context}"""
        
        message = client.messages.create(
            model=DEFAULT_ANTHROPIC_MODEL,
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_base64,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                }
            ],
        )
        
        response_text = message.content[0].text.strip()
        
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError:
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
            else:
                return {"isValid": True, "message": "", "note": "Impossible d'analyser l'image, génération autorisée."}
        
        is_product = data.get("isProduct", False)
        confidence = data.get("confidence", 0)
        reason = data.get("reason", "")
        
        if not is_product or confidence < 30:
            return {
                "isValid": False,
                "message": f"L'image ne semble pas correspondre à un produit commercial. {reason}",
                "confidence": confidence,
            }
        
        return {
            "isValid": True,
            "message": "",
            "confidence": confidence,
            "product_detected": reason,
        }
        
    except Exception as exc:
        print(f"[validate-image ERROR] {exc}")
        return {"isValid": True, "message": "", "note": f"Erreur de validation: {str(exc)}"}


@app.post("/api/search-by-image")
async def search_product_by_image(
    file: UploadFile = File(...),
    name: str = Form(""),
    utilisateurId: int = Form(...),
) -> Dict[str, Any]:
    """Search for product information by analyzing uploaded image with Claude Vision."""
    try:
        utilisateur_id = require_existing_user_id(utilisateurId)
        
        # Read image file
        image_data = await file.read()
        if not image_data:
            raise HTTPException(status_code=400, detail="Aucune image fournie.")
        
        # Validate image size (max 5MB)
        if len(image_data) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Image trop grande (max 5MB).")
        
        # Validate image type
        content_type = file.content_type or ""
        if not content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="Fichier invalide. Veuillez télécharger une image (JPEG, PNG, etc.).")
        
        # Convert to base64 for Claude Vision API
        image_base64 = base64.standard_b64encode(image_data).decode("utf-8")
        media_type = content_type if content_type else "image/jpeg"
        image_hash = hashlib.sha256(image_data).hexdigest()[:16]
        database_url = f"image-upload://{image_hash}"
        uploaded_image_url = save_uploaded_product_image(image_data, media_type, image_hash)
        
        # Call Claude to analyze image and extract product info
        metadata = extract_product_from_image(image_base64, media_type, name)
        
        # Get detected product name from Claude/user input. If it is still generic,
        # reverse image search will infer a name from Google result titles.
        detected_name = metadata.get("name") or name or ""
        if is_generic_product_name(detected_name) and not is_generic_product_name(name):
            detected_name = name
        
        # Step 1: Try to find the real product URL using Google reverse image search
        found_url = None
        inferred_price = ""
        if SERPAPI_KEY:
            # Use the detected product name for search
            search_query = "" if is_generic_product_name(detected_name) else detected_name
            reverse_result = search_product_by_image_google_result(image_base64, search_query)
            found_url = reverse_result.get("url", "")
            inferred_name = reverse_result.get("name", "")
            inferred_price = reverse_result.get("price", "")
            if is_generic_product_name(detected_name) and inferred_name:
                detected_name = inferred_name
                metadata["name"] = inferred_name
                search_query = inferred_name
            if inferred_price and not metadata.get("price"):
                metadata["price"] = inferred_price
            
            # Fallback: if reverse image search didn't work, try text search
            if not found_url and search_query:
                found_url = search_product_on_google(search_query)
        
        # Step 2: If we found a real URL, scrape it for accurate data (same flow as URL mode)
        if found_url and found_url.startswith(("http://", "https://")):
            print(f"[search-by-image] Found product URL via image search: {found_url}")
            try:
                html = fetch_page(found_url)
                scrape_metadata = extract_metadata(html, found_url, detected_name)
                scrape_metadata["url"] = found_url
                if inferred_price and not scrape_metadata.get("price"):
                    scrape_metadata["price"] = inferred_price
                if is_generic_product_name(detected_name):
                    detected_name = scrape_metadata.get("title") or detected_name
                verification = verify_image_product_match(
                    image_base64,
                    media_type,
                    detected_name,
                    scrape_metadata,
                )
                if not verification["accepted"]:
                    print(f"[search-by-image] Rejected candidate {found_url}: {verification}")
                    found_url = None
                    raise ValueError("Candidate product did not match uploaded image")
                
                # Use the detected name, but let scraping enhance it
                final_name = scrape_metadata.get("title") or detected_name
                base_product = make_marketing_product(final_name, found_url, scrape_metadata)
                base_product["similarityScore"] = verification["similarityScore"]
                base_product["verification"] = verification
                generated_product = enrich_product_with_claude(base_product, scrape_metadata)
                generated_product["similarityScore"] = verification["similarityScore"]
                generated_product["verification"] = verification
                
                scraped_images = filter_images_by_visual_similarity(
                    scrape_metadata.get("images", []),
                    image_base64,
                    media_type,
                    final_name,
                    IMAGE_SIMILARITY_THRESHOLD,
                )
                generated_product["image"] = uploaded_image_url
                generated_product["images"] = unique([uploaded_image_url, *scraped_images])
                
                product_id = save_product_to_database(generated_product, utilisateur_id)
                generated_product["id"] = product_id
                
                return {
                    "status": "ok",
                    "database": {
                        "saved": True,
                        "productId": product_id,
                    },
                    "product": generated_product,
                    "metadata": scrape_metadata,
                    "search_source": "scraped_from_found_url",
                    "found_url": found_url,
                    "verification": verification,
                }
            except HTTPException:
                raise
            except Exception as scrape_error:
                print(f"[search-by-image] Error scraping found URL {found_url}: {scrape_error}")
                # Fall through to fallback image-only flow
        elif found_url:
            print(f"[search-by-image] Found URL but invalid format: {found_url}")
        
        # Step 3: Fallback - use Claude vision analysis only (no real URL found)
        print(f"[search-by-image] No real product URL found, using image analysis only")
        fallback_name = detected_name if not is_generic_product_name(detected_name) else "Produit a identifier"
        fallback_tagline = metadata.get("tagline") if not is_generic_product_name(detected_name) else "Informations produit a completer"
        fallback_description = metadata.get("description") if not is_generic_product_name(detected_name) else ""
        base_product = {
            "name": detected_name or "Produit détecté",
            "tagline": metadata.get("tagline") or "Produit analysé depuis une image",
            "description": metadata.get("description") or "",
            "price": metadata.get("price") or "Prix non détecté",
            "image": uploaded_image_url,
            "images": metadata.get("images") or [uploaded_image_url],
            "brand": metadata.get("brand") or "",
            "features": metadata.get("features") or [],
            "technicalSpecs": metadata.get("technicalSpecs") or [],
            "reviews": metadata.get("reviews") or [],
            "cta": "Découvrir le produit",
            "url": database_url,
            "availability": metadata.get("availability") or "Vérifier la disponibilité",
            "rating": metadata.get("rating") or "",
            "category": metadata.get("category") or "Produit detecte par image",
            "sourceType": "image",
            "sections": {
                "benefitTitle": f"Pourquoi choisir ce produit ?",
                "proofTitle": "Informations détectées depuis l'image",
                "closingTitle": "Passez à l'action",
            },
        }
        
        # Search for additional images from multiple sources based on the product name
        if detected_name:
            additional_images = search_multiple_image_sources(
                detected_name,
                base_product.get("images", []),
                image_base64,
                media_type,
            )
            if additional_images:
                from itertools import chain
                merged_images = list(dict.fromkeys(chain(base_product.get("images", []), additional_images)))
                base_product["images"] = merged_images
                if merged_images[0] and (base_product.get("image") == DEFAULT_IMAGE or not base_product.get("image")):
                    base_product["image"] = merged_images[0]
        
        # Enrich with Claude marketing copy
        generated_product = enrich_product_with_claude(base_product, metadata)
        generated_product["image"] = uploaded_image_url
        generated_product["images"] = unique([uploaded_image_url, *base_product.get("images", [])])
        
        database_product = {
            **generated_product,
            "url": database_url,
        }
        product_id = save_product_to_database(database_product, utilisateur_id)
        generated_product["id"] = product_id
        generated_product["url"] = "#"
        
        return {
            "status": "ok",
            "database": {
                "saved": True,
                "productId": product_id,
            },
            "product": generated_product,
            "metadata": metadata,
            "search_source": "image_analysis_only",
            "found_url": None,
        }
    except HTTPException:
        raise
    except Exception as exc:
        import traceback
        error_trace = traceback.format_exc()
        print(f"[search_product_by_image ERROR] {error_trace}")
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors de l'analyse de l'image : {str(exc)}"
        )


def local_image_product_metadata(user_provided_name: str = "") -> Dict[str, Any]:
    product_name = clean_text(user_provided_name) or "Produit detecte"
    return {
        "name": product_name,
        "tagline": f"{product_name} pret pour une landing page mono-produit.",
        "description": (
            f"Une presentation claire de {product_name}, generee depuis l'image importee. "
            "Ajoutez une cle ANTHROPIC_API_KEY pour extraire automatiquement plus de details visuels."
        ),
        "price": "Non specifie",
        "brand": "",
        "features": [
            "Image produit integree a la page",
            "Structure mono-produit prete a exporter",
            "Sections marketing generees automatiquement",
            "Appel a l'action inclus",
        ],
        "technicalSpecs": [],
        "reviews": [],
        "category": "Produit detecte par image",
        "availability": "Verifier la disponibilite",
        "url": "#",
        "rating": "",
        "image": DEFAULT_IMAGE,
        "images": [],
    }


def extract_product_from_image(image_base64: str, media_type: str, user_provided_name: str = "") -> Dict[str, Any]:
    """Use Claude Vision to analyze image and extract product information."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return local_image_product_metadata(user_provided_name)

    try:
        import anthropic
        
        client = anthropic.Anthropic(api_key=api_key, timeout=20.0)
        
        prompt = f"""Vous analysez une image de produit. Extrayez les informations suivantes au format JSON strict:
- name: Nom du produit détecté (utiliser '{user_provided_name}' si fourni, sinon détecter depuis l'image)
- tagline: Courte description d'une ligne du produit
- description: Description détaillée du produit (2-3 phrases)
- price: Prix détecté (ou 'Non spécifié')
- features: Liste de 3-5 caractéristiques principales
- availability: État de disponibilité
- url: URL du produit si visible (sinon '#')
- rating: Note ou avis si visible (sinon '')

Répondez UNIQUEMENT avec du JSON valide, sans markdown ni explications."""
        
        message = client.messages.create(
            model=DEFAULT_ANTHROPIC_MODEL,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_base64,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                }
            ],
        )
        
        response_text = message.content[0].text.strip()
        
        # Try to parse JSON
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError:
            # Fallback: extract JSON if wrapped in markdown
            import re
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
            else:
                data = {}
        
        # Normalize the response
        return {
            "name": clean_text(data.get("name") or user_provided_name or "Produit détecté"),
            "tagline": clean_text(data.get("tagline") or "Produit analysé depuis une image."),
            "description": clean_text(data.get("description") or ""),
            "price": clean_text(data.get("price") or "Non spécifié"),
            "brand": clean_text(data.get("brand") or ""),
            "category": clean_text(data.get("category") or "Produit detecte par image"),
            "features": [clean_text(f) for f in (data.get("features") or []) if clean_text(f)],
            "technicalSpecs": data.get("technicalSpecs") if isinstance(data.get("technicalSpecs"), list) else [],
            "reviews": data.get("reviews") if isinstance(data.get("reviews"), list) else [],
            "availability": clean_text(data.get("availability") or "Vérifier la disponibilité"),
            "url": clean_text(data.get("url") or "#"),
            "rating": clean_text(data.get("rating") or ""),
            "image": DEFAULT_IMAGE,  # Use default since we can't extract from image file itself
            "images": [],
        }
    except Exception as exc:
        print(f"[extract_product_from_image ERROR] {exc}")
        return {
            "name": clean_text(user_provided_name) or "Produit détecté",
            "tagline": "Produit analysé depuis une image.",
            "description": "",
            "price": "Non spécifié",
            "brand": "",
            "category": "Produit detecte par image",
            "features": [],
            "technicalSpecs": [],
            "reviews": [],
            "availability": "Vérifier la disponibilité",
            "url": "#",
            "rating": "",
            "image": DEFAULT_IMAGE,
            "images": [],
        }

@app.post("/api/product")
def create_product(product: Dict[str, Any]) -> Dict[str, Any]:
    utilisateur_id = require_existing_user_id(product.get("utilisateurId"))
    validate_product_request(clean_text(product.get("name", "")), clean_text(product.get("url", "")))
    product_id = save_product_to_database(product, utilisateur_id)
    product["id"] = product_id
    return {
        "status": "ok",
        "database": {
            "saved": True,
            "productId": product_id,
        },
        "product": product,
    }

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR.parent), name="uploads")

static_dir = angular_browser_dir if angular_browser_dir.exists() else PROJECT_DIR / "front-end"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="front-end")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
