"""Credential management for OKX Quantitative Trading Gateway.

Stores credentials encrypted in local database.
Allows web-based credential input after startup.
"""

import json
import logging
import os
import base64
import secrets
from typing import Optional, Dict, Any, List
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CREDENTIALS_FILE = os.path.join(DATA_DIR, "credentials.json")
ENCRYPTION_KEY_FILE = os.path.join(DATA_DIR, ".encryption_key")

CREDENTIAL_TYPES = [
    "smtp_sender",
    "smtp_auth_code",
    "smtp_host",
    "smtp_port",
    "db_host",
    "db_port",
    "db_user",
    "db_password",
    "db_name",
    "okx_api_key",
    "okx_secret_key",
    "okx_passphrase",
    "api_key",
]

_credentials_cache: Dict[str, str] = {}
_initialized = False


def _get_encryption_key_bytes() -> bytes:
    """Get or create the encryption key from protected file."""
    os.makedirs(DATA_DIR, exist_ok=True)

    if os.path.exists(ENCRYPTION_KEY_FILE):
        try:
            with open(ENCRYPTION_KEY_FILE, "rb") as f:
                key_bytes = f.read()
            # Ensure key is exactly 32 bytes
            if len(key_bytes) == 32:
                return key_bytes
            else:
                logger.warning("Encryption key file has invalid length, regenerating")
        except Exception as e:
            logger.error("Failed to read encryption key file: %s", e)

    # Generate new key using secrets.token_bytes(32)
    key_bytes = secrets.token_bytes(32)
    try:
        with open(ENCRYPTION_KEY_FILE, "wb") as f:
            f.write(key_bytes)
        os.chmod(ENCRYPTION_KEY_FILE, 0o600)
        logger.info("Generated new encryption key at %s", ENCRYPTION_KEY_FILE)
    except Exception as e:
        logger.error("Failed to write encryption key file: %s", e)
        raise

    return key_bytes


def _get_fernet():
    """Get Fernet instance with proper key derivation."""
    from cryptography.fernet import Fernet
    key_bytes = _get_encryption_key_bytes()
    key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(key)


def _simple_encrypt(data: str) -> str:
    """Encrypt using Fernet."""
    if not data:
        return ""
    f = _get_fernet()
    return f.encrypt(data.encode()).decode()


def _simple_decrypt(encrypted: str) -> str:
    """Decrypt using Fernet."""
    if not encrypted:
        return ""
    try:
        f = _get_fernet()
        return f.decrypt(encrypted.encode()).decode()
    except Exception as e:
        logger.warning("Fernet decryption failed: %s", e)
        return ""


def _load_credentials_file() -> Dict[str, str]:
    if not os.path.exists(CREDENTIALS_FILE):
        return {}
    try:
        with open(CREDENTIALS_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to load credentials file: %s", e)
        return {}


def _save_credentials_file(creds: Dict[str, str]):
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(CREDENTIALS_FILE, "w") as f:
            json.dump(creds, f, indent=2)
        os.chmod(CREDENTIALS_FILE, 0o600)
    except Exception as e:
        logger.error("Failed to save credentials file: %s", e)
        raise


def init_credentials():
    global _credentials_cache, _initialized
    if _initialized:
        return

    # Ensure encryption key file has proper permissions
    if os.path.exists(ENCRYPTION_KEY_FILE):
        try:
            stat_info = os.stat(ENCRYPTION_KEY_FILE)
            permissions = stat_info.st_mode & 0o777
            if permissions != 0o600:
                logger.warning(
                    "Encryption key file has insecure permissions %o, fixing to 0o600",
                    permissions
                )
                os.chmod(ENCRYPTION_KEY_FILE, 0o600)
        except Exception as e:
            logger.error("Failed to check/set encryption key permissions: %s", e)
    else:
        # Trigger key generation if file doesn't exist
        _get_encryption_key_bytes()

    stored = _load_credentials_file()

    for cred_type, encrypted_value in stored.items():
        if cred_type in CREDENTIAL_TYPES:
            _credentials_cache[cred_type] = _simple_decrypt(encrypted_value)

    _initialized = True
    loaded_count = len([v for v in _credentials_cache.values() if v])
    logger.info("Loaded %d credentials from storage", loaded_count)


def get_credential(cred_type: str) -> Optional[str]:
    if not _initialized:
        init_credentials()
    return _credentials_cache.get(cred_type)


def set_credential(cred_type: str, value: str) -> bool:
    if cred_type not in CREDENTIAL_TYPES:
        logger.error("Invalid credential type: %s", cred_type)
        return False

    global _credentials_cache
    _credentials_cache[cred_type] = value

    stored = _load_credentials_file()
    stored[cred_type] = _simple_encrypt(value)
    _save_credentials_file(stored)

    logger.info("Saved credential: %s", cred_type)
    return True


def remove_credential(cred_type: str) -> bool:
    if cred_type not in CREDENTIAL_TYPES:
        return False

    global _credentials_cache
    _credentials_cache.pop(cred_type, None)

    stored = _load_credentials_file()
    stored.pop(cred_type, None)
    _save_credentials_file(stored)

    logger.info("Removed credential: %s", cred_type)
    return True


def get_credential_status() -> Dict[str, bool]:
    if not _initialized:
        init_credentials()
    return {cred_type: bool(_credentials_cache.get(cred_type)) for cred_type in CREDENTIAL_TYPES}


def get_smtp_config() -> Dict[str, Optional[str]]:
    return {
        "sender": get_credential("smtp_sender"),
        "auth_code": get_credential("smtp_auth_code"),
        "host": get_credential("smtp_host") or "smtp.163.com",
        "port": int(get_credential("smtp_port") or "465"),
        "enabled": bool(get_credential("smtp_sender") and get_credential("smtp_auth_code")),
    }


def get_db_config() -> Dict[str, Any]:
    return {
        "host": get_credential("db_host") or "127.0.0.1",
        "port": int(get_credential("db_port") or "3306"),
        "user": get_credential("db_user") or "okx_trader",
        "password": get_credential("db_password"),
        "database": get_credential("db_name") or "okx_gateway",
    }


def get_okx_config() -> Dict[str, Optional[str]]:
    return {
        "api_key": get_credential("okx_api_key"),
        "secret_key": get_credential("okx_secret_key"),
        "passphrase": get_credential("okx_passphrase"),
        "configured": bool(get_credential("okx_api_key")),
    }


def apply_credentials_to_env():
    """Apply stored credentials to environment variables."""
    smtp_sender = get_credential("smtp_sender")
    smtp_auth = get_credential("smtp_auth_code")

    if smtp_sender:
        os.environ["SMTP_SENDER"] = smtp_sender
    if smtp_auth:
        os.environ["SMTP_AUTH_CODE"] = smtp_auth

    logger.info("Applied credentials to environment variables")