#!/usr/bin/env python3
"""OKX Quantitative Trading Gateway - Entry Point.

Supports credential-free startup with web-based configuration.
Credentials are stored encrypted in local database.
"""
import atexit
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

from credential_manager import init_credentials, apply_credentials_to_env
init_credentials()
apply_credentials_to_env()

from network_manager import init_network, cleanup_network
if init_network():
    logger.info("Network subnet initialized")
else:
    logger.warning("Network subnet initialization failed - continuing without isolation")

atexit.register(cleanup_network)

from constants import (
    PROXY, PROXIES, OKX_BASE, CANDLE_BAR, CANDLE_LIMIT,
    INDICATOR_WARMUP, DEFAULT_INSTRUMENTS, build_genome,
)
from state import state, state_lock, save_state, load_state
from notifications import notify_signal as _notify_signal, send_trade_notification

STATE_DIRTY_REF = [False]


def notify_signal(*args, **kwargs):
    if len(args) == 0:
        return
    first_arg = args[0]
    if isinstance(first_arg, dict) and "safe_users" in first_arg:
        if len(args) >= 6:
            return _notify_signal(first_arg, args[1], args[2], args[3], args[4], args[5])
        elif len(args) == 5:
            return _notify_signal(first_arg, args[1], args[2], args[3], args[4], None)
        elif len(args) == 4:
            return _notify_signal(first_arg, args[1], args[2], args[3], None, None)
        elif len(args) == 3:
            return _notify_signal(first_arg, args[1], args[2], None, None, None)
        else:
            return _notify_signal(first_arg, args[1] if len(args) > 1 else None, None, None, None, None)
    else:
        if len(args) >= 4:
            return _notify_signal(state, args[0], args[1], args[2], args[3], args[4] if len(args) > 4 else None)
        elif len(args) == 3:
            return _notify_signal(state, args[0], args[1], args[2], None, None)
        else:
            return

from storage import pair_csv, load_candles, append_candles
from indicators import (
    calc_ema, calc_rsi, calc_macd, calc_bb, calc_stoch, calc_atr,
    calc_all_indicators, get_latest_indicators,
    compute_signal as _compute_signal,
)


def compute_signal(ind, genome=None):
    return _compute_signal(ind, genome or build_genome())

from okx_client import (
    fetch_candles, fetch_pair_candles, fetch_okx_ticker,
    calculate_for_pair as _calculate_for_pair, fetch_and_calculate_all, fetch_all_prices,
)

calculate_for_pair = lambda pair, candles: _calculate_for_pair(pair, candles, build_genome())

MAX_POSITIONS = 999999  # No limit — overtrading controlled via cooldown penalty
CIRCUIT_BREAK_LOSSES = 5
CIRCUIT_BREAK_DURATION = 900

from constants import NOTIFY_SENDER, NOTIFY_AUTH_CODE, NOTIFY_ENABLED

from concurrent.futures import ThreadPoolExecutor
import threads

load_state()

from okx_client import fetch_all_usdt_swap_instruments as _fetch_all_usdt_swap

if not state.get("trading_pairs"):
    all_instruments = _fetch_all_usdt_swap()
    with state_lock:
        state["trading_pairs"] = all_instruments
        STATE_DIRTY_REF[0] = True
    save_state()
    logger.info("trading_pairs populated with %d pairs", len(all_instruments))
else:
    logger.info("Loaded %d trading_pairs from saved state", len(state['trading_pairs']))

from flask import Flask
import routes

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(24)

import math
import json as _json
class _SafeEncoder(_json.JSONEncoder):
    def default(self, o):
        try:
            return super().default(o)
        except TypeError:
            return str(o)
    def encode(self, o):
        return super().encode(self._sanitize(o))
    def _sanitize(self, o):
        if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
            return 0.0
        if isinstance(o, dict):
            return {k: self._sanitize(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [self._sanitize(v) for v in o]
        return o
app.json_encoder = _SafeEncoder

routes.register_routes(
    app=app,
    state_ref=state,
    state_lock_ref=state_lock,
    compute_signal_ref=compute_signal,
    notify_signal_ref=notify_signal,
    fetch_pair_candles_ref=fetch_pair_candles,
    calculate_for_pair_ref=calculate_for_pair,
    fetch_all_prices_ref=fetch_all_prices,
    save_state_ref=save_state,
    state_dirty_ref=STATE_DIRTY_REF,
    MAX_POSITIONS=999999,
)

# Background threads for trading only (data comes from separate engine process)
threads.start_strategy_tick(state_ref=state, state_lock=state_lock, genome=build_genome(),
                            notify_signal_func=notify_signal, save_state_func=save_state)
threads.start_state_persistor(state_ref=state, state_lock=state_lock, save_state_func=save_state)

from model_manager import get_manager as get_model_manager
model_mgr = get_model_manager()
model_mgr.start_watcher(interval=30)
logger.info("Model watcher started (30s interval)")

if __name__ == "__main__":
    from credential_manager import get_credential_status
    creds = get_credential_status()
    configured_count = sum(1 for v in creds.values() if v)

    print("=" * 60)
    print("  OKX Quantitative Trading Gateway v2.00")
    print(f"  Local: http://127.0.0.1:5000")
    import subprocess
    try:
        wsl_ip = subprocess.check_output("hostname -I | awk '{print $1}'", shell=True).decode().strip()
        print(f"  WSL:   http://{wsl_ip}:5000")
    except Exception:
        pass
    print(f"  Credentials configured: {configured_count}/{len(creds)}")
    if configured_count == 0:
        print("  ⚠️  No credentials configured - use web interface to set up")
    print("=" * 60)

    from waitress import serve
    serve(app, host="0.0.0.0", port=5000)