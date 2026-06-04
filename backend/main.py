import json
import hashlib
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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

try:
    import mysql.connector
    from mysql.connector import Error as MySQLError
except ImportError:
    mysql = None
    MySQLError = Exception

app = FastAPI(title="Mono Product Landing Generator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = Path(__file__).resolve().parent.parent / "front-end"
app.mount("/ui", StaticFiles(directory=static_dir, html=True), name="front-end")

DEFAULT_IMAGE = "https://images.unsplash.com/photo-1516321318423-f06f85e504b3?auto=format&fit=crop&w=1200&q=80"
PROJECT_DIR = Path(__file__).resolve().parent.parent
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5"


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
    "motDePasse": os.environ.get("ADMIN_PASSWORD", ""),
}


class ProductMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.description = ""
        self.image = ""
        self.meta: Dict[str, str] = {}
        self.json_ld_blocks: List[str] = []
        self.text_chunks: List[str] = []
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
        elif self._current_tag in {"h1", "h2", "h3", "p", "li", "span"} and len(text) > 2:
            self.text_chunks.append(text)


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
                image TEXT NULL,
                images LONGTEXT NULL,
                urlProduit VARCHAR(2048) NOT NULL,
                dateCreation TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_url_produit (urlProduit(768)),
                INDEX idx_produits_utilisateur (utilisateur_id),
                CONSTRAINT fk_produits_utilisateur
                    FOREIGN KEY (utilisateur_id) REFERENCES utilisateurs(id)
                    ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

            INSERT INTO utilisateurs (nom, email, motDePasse)
            VALUES ({nom}, {email}, {password})
            ON DUPLICATE KEY UPDATE nom = VALUES(nom);
            """.format(
                nom=sql_literal(DEFAULT_ADMIN["nom"]),
                email=sql_literal(DEFAULT_ADMIN["email"]),
                password=sql_literal(DEFAULT_ADMIN["motDePasse"]),
            )
        )
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
                    image TEXT NULL,
                    images LONGTEXT NULL,
                    urlProduit VARCHAR(2048) NOT NULL,
                    dateCreation TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_url_produit (urlProduit(768)),
                    INDEX idx_produits_utilisateur (utilisateur_id),
                    CONSTRAINT fk_produits_utilisateur
                        FOREIGN KEY (utilisateur_id) REFERENCES utilisateurs(id)
                        ON DELETE SET NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            cursor.execute(
                """
                INSERT INTO utilisateurs (nom, email, motDePasse)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE nom = VALUES(nom)
                """,
                (DEFAULT_ADMIN["nom"], DEFAULT_ADMIN["email"], DEFAULT_ADMIN["motDePasse"]),
            )
        connection.commit()


def get_default_admin_id(connection) -> int:
    with closing(connection.cursor()) as cursor:
        cursor.execute("SELECT id FROM utilisateurs WHERE email = %s", (DEFAULT_ADMIN["email"],))
        row = cursor.fetchone()
        if row:
            return int(row[0])

        cursor.execute(
            "INSERT INTO utilisateurs (nom, email, motDePasse) VALUES (%s, %s, %s)",
            (DEFAULT_ADMIN["nom"], DEFAULT_ADMIN["email"], DEFAULT_ADMIN["motDePasse"]),
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

    validate_product_request(name, url)

    if mysql is None:
        images_json = json.dumps(images if isinstance(images, list) else [], ensure_ascii=False)
        output = run_mysql_cli(
            """
            INSERT INTO produits (utilisateur_id, nom, description, image, images, urlProduit)
            VALUES (
                {user_id},
                {name},
                {description},
                {image},
                {images},
                {url}
            )
            ON DUPLICATE KEY UPDATE
                id = LAST_INSERT_ID(id),
                utilisateur_id = VALUES(utilisateur_id),
                nom = VALUES(nom),
                description = VALUES(description),
                image = VALUES(image),
                images = VALUES(images);
            SELECT LAST_INSERT_ID();
            """.format(
                user_id=user_id,
                name=sql_literal(name),
                description=sql_literal(description),
                image=sql_literal(image),
                images=sql_literal(images_json),
                url=sql_literal(url),
            )
        )
        last_line = output.splitlines()[-1] if output else "0"
        return int(last_line) if last_line.isdigit() else 0

    with closing(connect_mysql()) as connection:
        with closing(connection.cursor()) as cursor:
            cursor.execute(
                """
                INSERT INTO produits (utilisateur_id, nom, description, image, images, urlProduit)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    utilisateur_id = VALUES(utilisateur_id),
                    nom = VALUES(nom),
                    description = VALUES(description),
                    image = VALUES(image),
                    images = VALUES(images)
                """,
                (
                    user_id,
                    name,
                    description,
                    image,
                    json.dumps(images if isinstance(images, list) else [], ensure_ascii=False),
                    url,
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
    attr_pattern = r'(?:src|data-src|data-original|data-lazy-src|content)=["\']([^"\']+)["\']'
    srcset_pattern = r'(?:srcset|data-srcset)=["\']([^"\']+)["\']'

    for tag_match in re.finditer(r"<(?:img|source|meta|link)\b[^>]*>", html, flags=re.I):
        tag = tag_match.group(0)
        if re.search(r"<meta\b", tag, flags=re.I) and not re.search(
            r'(?:property|name|itemprop)=["\'](?:og:image|og:image:url|twitter:image|image)["\']',
            tag,
            flags=re.I,
        ):
            continue

        for match in re.finditer(attr_pattern, tag, flags=re.I):
            url = normalize_image_url(match.group(1), base_url)
            if url:
                images.append(url)

        for match in re.finditer(srcset_pattern, tag, flags=re.I):
            images.extend(extract_srcset_urls(match.group(1), base_url))

    return unique(images)


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
        r"(logo|icon|sprite|placeholder|avatar|favicon|payment|badge|trade|tradein|trade-in|carrier|financ|compare|setup|support|store|banner)",
        lower,
        flags=re.I,
    ):
        return -20
    if lower.endswith((".svg", ".ico", ".gif")):
        return -20

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
    price = extract_price_from_json_ld(product_json) or parse_price(html)

    parser_image = normalize_image_url(parser.image, base_url)
    primary_image = json_images[0] if json_images else parser_image
    image_product_name = clean_text(requested_name) or title
    images = filter_product_images(unique([*json_images, *images, parser_image]), image_product_name, primary_image)
    image = images[0] if images else select_best_image(images, parser_image)
    availability = extract_availability_from_json_ld(product_json)
    rating = extract_rating(product_json)
    features = build_features(title, description, parser.text_chunks, product_json)

    return {
        "title": title,
        "description": description or f"{title} est prêt à être présenté dans une landing page mono-produit.",
        "price": price,
        "image": image,
        "images": images,
        "availability": availability,
        "rating": rating,
        "features": features,
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
        "image": metadata.get("image") or DEFAULT_IMAGE,
        "images": metadata.get("images") or [],
        "features": metadata.get("features") or [],
        "cta": "Voir le produit",
        "url": url,
        "availability": metadata.get("availability") or "Disponibilité à vérifier",
        "rating": metadata.get("rating") or "",
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
        }
    }
    return (
        "Tu es un copywriter e-commerce senior francophone. "
        "Genere du contenu marketing et SEO pour une landing page mono-produit. "
        "N'invente pas de prix, de disponibilite, de note ou de caracteristique technique non fournie. "
        "Retourne uniquement un objet JSON valide avec les cles suivantes: "
        "name, tagline, description, features, cta, seoTitle, seoDescription, sections. "
        "features doit contenir 4 a 6 avantages courts. "
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
    utilisateur_id = require_existing_user_id(product.get("utilisateurId"))
    name = clean_text(product.get("name", ""))
    url = clean_text(product.get("url", ""))

    validate_product_request(name, url)

    html = fetch_page(url)
    metadata = extract_metadata(html, url, name)
    validate_product_match(name, url, metadata)
    base_product = make_marketing_product(name, url, metadata)
    generated_product = enrich_product_with_claude(base_product, metadata)
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
