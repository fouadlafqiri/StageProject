CREATE DATABASE IF NOT EXISTS mono_product_stage
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE mono_product_stage;

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
VALUES ('Administrateur', 'admin@localhost.test', '')
ON DUPLICATE KEY UPDATE nom = VALUES(nom);
