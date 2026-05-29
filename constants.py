"""OKX Quantitative Trading Gateway - Configuration (v2.00).

All credentials from environment variables - no hardcoded secrets.
Strategy parameters loaded from database/config file.
"""

import os
import logging

logger = logging.getLogger(__name__)

VERSION = "v2.00"

_raw_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
if not _raw_proxy:
    try:
        import subprocess
        # Use bashrc's method: ip route show default | awk '/default/ {print $3}'
        result = subprocess.run(
            ["bash", "-c", "ip route show default | awk '/default/ {print $3}'"],
            capture_output=True, text=True, timeout=5
        )
        wsl_host = result.stdout.strip()
        if wsl_host:
            _raw_proxy = f"http://{wsl_host}:7890"
    except Exception:
        pass
PROXY = _raw_proxy if _raw_proxy else None
PROXIES = {"https://": PROXY, "http://": PROXY} if PROXY else None

OKX_BASE = os.environ.get("OKX_BASE_URL", "https://www.okx.com")
CANDLE_BAR = "4H"
CANDLE_LIMIT = 200
INDICATOR_WARMUP = 300

from strategy_manager import (
    get_strategy_params, init_strategy_params,
    get_regime_mode, get_all_regimes, get_general_params,
)

init_strategy_params()

STRATEGY_CONFIG = get_strategy_params()


def build_genome():
    """Build v274 LightGBM genome from strategy_manager."""
    general = get_general_params()
    general.setdefault("leverage", 3.0)
    general.setdefault("fee", 0.0015)
    return {
        "regime_mode": get_regime_mode(),
        "regimes": get_all_regimes(),
        "general": general,
        "leverage": general.get("leverage", 3.0),
        "fee": general.get("fee", 0.0015),
    }



NOTIFY_SENDER = os.environ.get("SMTP_SENDER")
NOTIFY_AUTH_CODE = os.environ.get("SMTP_AUTH_CODE")
NOTIFY_ENABLED = bool(NOTIFY_AUTH_CODE) and bool(NOTIFY_SENDER)

if not NOTIFY_ENABLED:
    logger.info("Email notifications disabled: SMTP_SENDER and/or SMTP_AUTH_CODE not set")

DEFAULT_INSTRUMENTS = [
    "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP",
    "ADA-USDT-SWAP", "AVAX-USDT-SWAP", "LINK-USDT-SWAP", "DOT-USDT-SWAP",
    "UNI-USDT-SWAP", "ATOM-USDT-SWAP", "FIL-USDT-SWAP", "NEAR-USDT-SWAP", "APT-USDT-SWAP",
    "ARB-USDT-SWAP", "SAND-USDT-SWAP", "MANA-USDT-SWAP", "AAVE-USDT-SWAP", "GRT-USDT-SWAP",
    "INJ-USDT-SWAP", "GALA-USDT-SWAP", "IMX-USDT-SWAP", "ALGO-USDT-SWAP",
    "DASH-USDT-SWAP", "COMP-USDT-SWAP", "BAT-USDT-SWAP", "ENJ-USDT-SWAP", "LRC-USDT-SWAP",
    "1INCH-USDT-SWAP", "BONK-USDT-SWAP", "WIF-USDT-SWAP", "PEPE-USDT-SWAP",
    "NEIRO-USDT-SWAP", "WLD-USDT-SWAP", "ICX-USDT-SWAP",
]
