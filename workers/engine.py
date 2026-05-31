#!/usr/bin/env python3
"""v274 Signal Engine — standalone computation service (port 5001).

Fetches 4H candles, computes indicators, runs LightGBM model, normalizes scores.
Gateway reads signals from this engine via HTTP API.
"""
import os, sys, time, json, logging, threading
import pandas as pd, numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ENGINE] %(message)s")
logger = logging.getLogger("engine")

from flask import Flask, jsonify, request
app = Flask(__name__)

from indicators.lgbm import _load_model, compute_signal as lgbm_raw, get_params, is_loaded
from exchange.client import fetch_pair_candles, fetch_all_usdt_swap_instruments
from indicators.calculator import calc_all_indicators, get_latest_indicators, compute_signal
from core.constants import build_genome, CANDLE_BAR, CANDLE_LIMIT, DEFAULT_INSTRUMENTS
from core.storage import load_candles, pair_csv
from core.state import state as gateway_state, state_lock

ENGINE_PORT = int(os.environ.get("ENGINE_PORT", "5001"))
UPDATE_INTERVAL = 7200  # 2h

_signal_cache = {}
_last_update = "never"
_timing = {"fetch_ms": 0, "calc_ms": 0, "total_ms": 0, "pairs": 0}
_refreshing = False
_pair_count = 0


def _fetch_and_compute():
    global _signal_cache, _last_update, _timing, _refreshing, _pair_count
    _refreshing = True
    t0 = time.time()

    try:
        instruments = fetch_all_usdt_swap_instruments()
    except Exception as e:
        logger.warning("Instrument fetch failed: %s", e)
        instruments = []

    if not instruments:
        with state_lock:
            instruments = list(gateway_state.get("trading_pairs", DEFAULT_INSTRUMENTS))

    _pair_count = len(instruments)
    logger.info("Fetching %d pairs", _pair_count)

    import asyncio, httpx
    async def _fetch_all():
        sem = asyncio.Semaphore(12)
        async def _fetch_one(client, pair):
            async with sem:
                return pair, await fetch_pair_candles(client, pair, retries=2)
        from constants import PROXY
        async with httpx.AsyncClient(timeout=60.0, proxy=PROXY) as client:
            tasks = [_fetch_one(client, p) for p in instruments]
            return await asyncio.gather(*tasks, return_exceptions=True)

    loop = asyncio.new_event_loop()
    try:
        all_results = loop.run_until_complete(_fetch_all())
    finally:
        loop.close()

    t_fetch = time.time()
    fetch_ms = round((t_fetch - t0) * 1000)

    genome = build_genome()
    results = {}
    pair_candles = []

    for r in all_results:
        if isinstance(r, tuple) and len(r) == 2:
            pair_candles.append(r)

    valid = [(p, c) for p, c in pair_candles if isinstance(c, list) and len(c) >= 10]

    if not _load_model():
        logger.error("Model not loaded")
        _refreshing = False
        return

    for pair, candles in valid:
        try:
            indicators = calc_all_indicators(candles, pair=pair)
            latest = get_latest_indicators(indicators)
            _, model_raw, _ = lgbm_raw(candles)
            ind_sig, ind_long, ind_short = compute_signal(latest, genome)
            n = len(candles)
            results[pair] = {
                "pair": pair, "signal": "HOLD",
                "norm_long": float(ind_long), "norm_short": float(ind_short),
                "model_raw": float(model_raw),
                "rsi_14": latest.get("rsi_14", 50),
                "rsi_7": latest.get("rsi_7", 50),
                "macd_hist": latest.get("macd_hist", 0),
                "bb_position": latest.get("bb_position", 0.5),
                "stoch_k": latest.get("stoch_k", 50),
                "ema_25": latest.get("ema_25", 0),
                "ema_50": latest.get("ema_50", 0),
                "atr_14": latest.get("atr_14", 1),
                "close": latest.get("close", 0),
                "ts": latest.get("timestamps", 0),
                "kline_ts": latest.get("timestamps", 0),
                "sl": 0, "tp": 0,
                "data_points": n,
                "error": None,
            }
        except Exception as e:
            results[pair] = {"pair": pair, "signal": "HOLD", "data_points": 0, "error": str(e)}

    # Batch normalize
    all_sc = [(p, max(r.get("norm_long", 0), r.get("norm_short", 0)))
              for p, r in results.items() if r.get("data_points", 0) > 0]
    if all_sc:
        mn = min(v for _, v in all_sc)
        mx = max(v for _, v in all_sc)
        rng = mx - mn if mx > mn else 1.0
        sorted_ps = sorted(all_sc, key=lambda x: -x[1])
        top_n = max(1, len(sorted_ps) // 2)
        for i, (pair, _) in enumerate(sorted_ps):
            if pair in results:
                results[pair]["score"] = round((sorted_ps[i][1] - mn) / rng * 100, 1)
                results[pair]["signal"] = "LONG" if i < top_n else "HOLD"

    t_calc = time.time()
    _signal_cache = results
    _last_update = time.strftime("%Y-%m-%d %H:%M:%S")
    _timing = {"fetch_ms": fetch_ms, "calc_ms": round((t_calc - t_fetch) * 1000),
               "total_ms": round((t_calc - t0) * 1000), "pairs": _pair_count}
    _refreshing = False
    logger.info("Engine updated: %d pairs, %d LONG", len(results),
                sum(1 for r in results.values() if r.get("signal") == "LONG"))


def _update_loop():
    _fetch_and_compute()
    while True:
        time.sleep(UPDATE_INTERVAL)
        try:
            _fetch_and_compute()
        except Exception as e:
            logger.error("Update failed: %s", e)


# ── API endpoints ──

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "engine": "v274", "pairs": _pair_count,
                    "updated": _last_update, "candle_bar": CANDLE_BAR})


@app.route("/api/signals")
def signals():
    g = build_genome()
    if "regimes" in g:
        thresholds = {"regime_mode": g.get("regime_mode", "auto"),
                      "regimes": {r: dict(p.items()) for r, p in g["regimes"].items()}}
    else:
        thresholds = {"score_min": g.get("score_min", 0)}
    return jsonify({"signals": _signal_cache, "thresholds": thresholds,
                    "last_update": _last_update, "computing": _refreshing})


@app.route("/api/indicators")
def indicators():
    return jsonify({"pair_count": _pair_count, "last_update": _last_update,
                    "refreshing": _refreshing, "timing": _timing,
                    "interval_seconds": UPDATE_INTERVAL, "indicators": {}})


@app.route("/api/strategy/model")
def model_info():
    if not is_loaded():
        return jsonify({"loaded": False})
    p = get_params()
    return jsonify({"loaded": True, "version": p["version"], "champion": p["champion"],
                    "model": p["model_params"], "trade": p["trade_params"],
                    "n_features": p["n_features"]})


if __name__ == "__main__":
    logger.info("Starting v274 Signal Engine on port %d", ENGINE_PORT)
    threading.Thread(target=_update_loop, daemon=True).start()
    app.run(host="127.0.0.1", port=ENGINE_PORT, debug=False, threaded=True)
