-- MySQL init for OKX Gateway — run once on first container start
-- Creates the okx_gateway database (if MYSQL_DATABASE didn't handle it)
-- All tables are auto-created by database.py's init_database()

CREATE DATABASE IF NOT EXISTS okx_gateway
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

GRANT ALL PRIVILEGES ON okx_gateway.* TO 'okx_trader'@'mysql';
FLUSH PRIVILEGES;
