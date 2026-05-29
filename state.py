"""Global state management for OKX Quantitative Trading Gateway."""

import json
import logging
import os
import threading
from typing import Any

from credential_manager import _simple_decrypt, _simple_encrypt

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

GATEWAY_STATE_FILE = os.path.join(DATA_DIR, "gateway_state.json")

state_lock = threading.Lock()
_state_dirty = threading.Event()

state = {
    "mode": "safe",
    "safe_users": [],
    "running_accounts": [],
    "trading_pairs": [
        "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP",
        "DOGE-USDT-SWAP", "ADA-USDT-SWAP", "AVAX-USDT-SWAP", "LINK-USDT-SWAP",
        "POL-USDT-SWAP", "DOT-USDT-SWAP", "LTC-USDT-SWAP", "UNI-USDT-SWAP",
        "ATOM-USDT-SWAP", "THETA-USDT-SWAP", "XLM-USDT-SWAP", "TRX-USDT-SWAP",
        "FIL-USDT-SWAP", "NEAR-USDT-SWAP", "APT-USDT-SWAP", "ARB-USDT-SWAP",
        "OP-USDT-SWAP", "SAND-USDT-SWAP", "MANA-USDT-SWAP", "AAVE-USDT-SWAP",
        "SHIB-USDT-SWAP", "GRT-USDT-SWAP", "SUI-USDT-SWAP", "STX-USDT-SWAP",
        "INJ-USDT-SWAP", "WLD-USDT-SWAP", "PEPE-USDT-SWAP", "WOO-USDT-SWAP",
    ],
    "strategy_active": True,
    "strategy_log": [],
    "price_cache": {},
    "price_updated": "never",
    "price_fetch_ms": 0,
    "last_error": None,
    "indicators_refreshing": False,
    "indicator_cache": {},
    "data_status": {},
    "open_positions": [],
    "position_history": [],
    "pending_signals": [],
    "consecutive_losses": 0,
    "circuit_broken": False,
    "circuit_broken_until": None,
}

STATE_PERSIST_KEYS = [
    "mode", "safe_users", "trading_pairs",
    "strategy_active", "open_positions", "position_history", "pending_signals",
    "consecutive_losses", "circuit_broken", "circuit_broken_until",
]


def mark_dirty():
    _state_dirty.set()


def is_dirty() -> bool:
    return _state_dirty.is_set()


def save_state():
    try:
        with state_lock:
            persist = {k: state[k] for k in STATE_PERSIST_KEYS if k in state}
            running_accounts = list(state.get("running_accounts", []))
        if running_accounts:
            for acct in running_accounts:
                for key_field in ("api_key", "api_secret", "passphrase"):
                    if key_field in acct and acct[key_field]:
                        acct[key_field] = _simple_encrypt(str(acct[key_field]))
            persist["running_accounts_encrypted"] = running_accounts
        with open(GATEWAY_STATE_FILE, "w") as f:
            json.dump(persist, f, indent=2)
        os.chmod(GATEWAY_STATE_FILE, 0o600)
        _state_dirty.clear()
    except Exception as e:
        logging.warning("[SAVE_STATE] Failed to persist state: %s", e)


def load_state():
    try:
        if os.path.exists(GATEWAY_STATE_FILE):
            with open(GATEWAY_STATE_FILE, "r") as f:
                loaded = json.load(f)
            with state_lock:
                for k, v in loaded.items():
                    if k in STATE_PERSIST_KEYS:
                        state[k] = v
                if "running_accounts_encrypted" in loaded:
                    running_accounts = loaded["running_accounts_encrypted"]
                    for acct in running_accounts:
                        for key_field in ("api_key", "api_secret", "passphrase"):
                            if key_field in acct and acct[key_field]:
                                acct[key_field] = _simple_decrypt(str(acct[key_field]))
                    state["running_accounts"] = running_accounts
                elif "running_accounts" in loaded:
                    state["running_accounts"] = loaded["running_accounts"]
    except Exception as e:
        logging.warning(f"[LOAD_STATE] Failed to load persisted state: {e}")


def get_state(key: str, default: Any = None) -> Any:
    with state_lock:
        return state.get(key, default)


def set_state(key: str, value: Any) -> None:
    with state_lock:
        state[key] = value
    mark_dirty()


load_state()