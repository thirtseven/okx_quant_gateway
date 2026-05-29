"""OKX API client for fetching candles and ticker data.

Optimized version with:
- Delta candle fetching (only fetch new candles)
- Incremental indicator calculation
- Higher concurrency (10 concurrent requests)
- Indicator value caching
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import httpx

from constants import OKX_BASE, CANDLE_BAR, CANDLE_LIMIT, PROXY
from indicators import calc_all_indicators, get_latest_indicators, safe_val


# Statistics tracking
_proxy_stats = {"success": 0, "failure": 0, "last_proxy": None}

def report_success(proxy=None):
    _proxy_stats["success"] += 1
    _proxy_stats["last_proxy"] = proxy

def report_failure(proxy=None):
    _proxy_stats["failure"] += 1
    _proxy_stats["last_proxy"] = proxy


# Candle cache: {pair: {"candles": [...], "last_ts": int, "ts": float}}
_candle_cache = {}
_cache_lock = Lock()
_CACHE_TTL_MS = 120000
_MAX_CONCURRENT = 5  # OKX public rate limit: ~20req/2s, 5 concurrent is safe


def pair_csv(pair):
    from storage import pair_csv as _pcsv
    return _pcsv(pair)


def load_candles(pair):
    from storage import load_candles as _lc
    return _lc(pair)


def append_candles(pair, candles):
    from storage import append_candles as _ac
    return _ac(pair, candles)


def warmup_cache_from_csv(pairs):
    from storage import warmup_cache_from_csvs
    warmed = 0
    for pair, rows in warmup_cache_from_csvs(pairs).items():
        if rows:
            last_ts = int(rows[-1]["ts"])
            with _cache_lock:
                _candle_cache[pair] = {"candles": rows[-300:], "last_ts": last_ts, "ts": time.time() * 1000}
            warmed += 1
    logging.info("[CACHE] Warmed up %d/%d pairs from CSV", warmed, len(pairs))


def _validate_candle(c):
    try:
        ts = int(c[0])
        o = float(c[1])
        h = float(c[2])
        l = float(c[3])
        c_val = float(c[4])
        vol = float(c[5])
        if h < l or o <= 0 or h <= 0 or l <= 0 or c_val <= 0:
            return False
        if abs(h) > 1e7 or abs(l) > 1e7 or abs(c_val) > 1e7 or abs(o) > 1e7:
            return False
        return True
    except (ValueError, TypeError, IndexError):
        return False


async def fetch_candles(client, inst_id, after_ms=None, before_ms=None, limit=CANDLE_LIMIT):
    try:
        params = {"instId": inst_id, "bar": CANDLE_BAR, "limit": limit}
        if after_ms:
            params["after"] = str(after_ms)
        elif before_ms:
            params["before"] = str(before_ms)
        url = f"{OKX_BASE}/api/v5/market/candles"
        r = await client.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=20.0)
        d = r.json()
        
        if d.get("code") != "0":
            logging.warning(f"[FETCH_CANDLES] {inst_id} API error: code={d.get('code')}, msg={d.get('msg', 'unknown')}")
            return []
        
        if not d.get("data"):
            logging.warning(f"[FETCH_CANDLES] {inst_id} empty data array: code={d.get('code')}, data={d.get('data')}")
            return []
        
        candles = []
        for c in reversed(d["data"]):
            if _validate_candle(c):
                candles.append({
                    "ts": int(c[0]),
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "vol": float(c[5]),
                })
        
        if not candles:
            logging.warning(f"[FETCH_CANDLES] {inst_id} all {len(d['data'])} candles failed validation")
            return []
            
        return candles
    except Exception as e:
        logging.warning(f"[FETCH_CANDLES] {inst_id} exception: {type(e).__name__}: {e}")
    return []


STALE_THRESHOLD_MS = 86400 * 1000


async def fetch_pair_candles(client, pair, retries=2):
    """Fetch candles for a pair with delta optimization.
    
    If we have valid cached candles (not expired), only fetch new ones since last_ts.
    Otherwise, do a full fetch.
    """
    last_error = None
    
    # Get cached info - check both last_ts AND cache validity
    cached_last_ts = 0
    cache_valid = False
    with _cache_lock:
        entry = _candle_cache.get(pair)
        if entry:
            cached_last_ts = entry.get("last_ts", 0)
            # Cache is valid if TTL hasn't expired AND we have enough candles
            cache_valid = (time.time() * 1000 - entry["ts"] < _CACHE_TTL_MS 
                          and len(entry.get("candles", [])) >= 200)
    
    for attempt in range(retries + 1):
        # Only use delta fetch if cache is valid (not expired, has enough candles)
        if cache_valid and cached_last_ts > 0 and attempt == 0:
            candles = await fetch_candles(client, pair, after_ms=cached_last_ts, limit=100)
            if candles and len(candles) > 0:
                cached = _get_cached_candles(pair)
                if cached:  # Should always be true since cache_valid
                    merged = _merge_candles_on_timeline(cached, candles)
                    _update_cached_candles(pair, merged)
                    return merged
            # Delta fetch failed, fall through to full fetch
        
        # Full fetch
        candles = await fetch_candles(client, pair, limit=CANDLE_LIMIT)
        if not candles:
            last_error = f"attempt {attempt+1}: no candles returned"
            if attempt < retries:
                await asyncio.sleep(1.0 * (attempt + 1))
            continue
        
        if len(candles) < 10:
            last_error = f"only {len(candles)} candles (need >=10)"
            if attempt < retries:
                await asyncio.sleep(0.5)
                continue
        
        last_close = candles[-1].get("close", 0)
        if last_close == 0:
            last_error = "last_close=0 (stale data)"
            if attempt < retries:
                await asyncio.sleep(1.0)
                continue
        
        if last_close < 0:
            last_error = f"invalid last_close={last_close}"
            if attempt < retries:
                await asyncio.sleep(0.5)
                continue
        
        cached = _get_cached_candles(pair)
        merged = _merge_candles_on_timeline(cached, candles)
        _update_cached_candles(pair, merged)
        
        return merged
    
    logging.warning(f"[FETCH_PAIR_CANDLES] {pair} failed after {retries+1} attempts: {last_error}")
    return []


def _get_cached_candles(pair):
    with _cache_lock:
        entry = _candle_cache.get(pair)
        if entry and time.time() * 1000 - entry["ts"] < _CACHE_TTL_MS:
            return entry["candles"]
    return []


def _merge_candles_on_timeline(cached, new_candles):
    ts_map = {}
    for c in cached:
        ts_map[c["ts"]] = c
    for c in new_candles:
        ts_map[c["ts"]] = c
    return sorted(ts_map.values(), key=lambda x: x["ts"])


def _update_cached_candles(pair, candles):
    last_ts = candles[-1]["ts"] if candles else 0
    with _cache_lock:
        _candle_cache[pair] = {"candles": candles, "last_ts": last_ts, "ts": time.time() * 1000}
    append_candles(pair, candles)
    return candles


async def fetch_okx_ticker(client, inst_id):
    url = f"{OKX_BASE}/api/v5/market/ticker?instId={inst_id}"
    headers = {"User-Agent": "Mozilla/5.0"}
    last_error = None
    
    for attempt in range(3):
        try:
            r = await client.get(url, headers=headers, timeout=8.0)
            d = r.json()
            if d.get("code") == "0" and d.get("data"):
                data = d["data"][0]
                last_str = data.get("last", "--")
                last_val = float(last_str) if last_str not in ("--", "") else 0
                if last_val == 0:
                    await asyncio.sleep(0.5)
                    continue
                return {
                    "inst_id": inst_id,
                    "last": last_str,
                    "open24h": data.get("open24h", "--"),
                    "vol24h": data.get("vol24h", "--"),
                    "ts": data.get("ts", ""),
                }
            else:
                last_error = f"API error: code={d.get('code')}, msg={d.get('msg', 'unknown')}"
                logging.warning(f"[FETCH_TICKER] {inst_id} {last_error}")
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
        except httpx.TimeoutException:
            last_error = f"timeout after 8s"
            logging.warning(f"[FETCH_TICKER] {inst_id} timeout")
            if attempt < 2:
                await asyncio.sleep(0.5)
                continue
        except httpx.ProxyError as e:
            last_error = f"proxy error: {e}"
            logging.warning(f"[FETCH_TICKER] {inst_id} proxy error: {e}")
            break
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            logging.warning(f"[FETCH_TICKER] {inst_id} failed: {last_error}")
            if attempt < 2:
                await asyncio.sleep(0.5)
                continue
    
    return None


def _prec(val, min_dp=10):
    if val == 0:
        return 0.0
    return round(val, 10)


# Result cache: {(pair, last_ts): result_dict}
_result_cache = {}
_result_cache_lock = Lock()


def calculate_for_pair(pair, candles, genome):
    cached = _result_cache.get((pair, candles[-1]["ts"] if candles else 0))
    if cached is not None:
        return cached

    indicators = calc_all_indicators(candles, pair=pair)
    latest = get_latest_indicators(indicators)
    # v274 model for direction, indicator scores for granular ranking
    from lgbm_bridge import compute_signal as v274_raw
    _, model_score, _ = v274_raw(candles)
    from indicators import compute_signal as indicator_score
    ind_sig, ind_long, ind_short = indicator_score(latest, genome)
    regime = genome.get("regime_mode", "auto")
    if regime in ("bull", "bear", "range"):
        score_min = genome.get("regimes", {}).get(regime, {}).get("score_min", 1.0)
    else:
        score_min = 1.0
    raw_score = max(ind_long, ind_short)
    if model_score > 0 and raw_score >= score_min:
        signal = "LONG"
    elif model_score < 0 and raw_score >= score_min:
        signal = "SHORT"
    else:
        signal = "HOLD"
    norm_long, norm_short = float(ind_long), float(ind_short)

    close_val = safe_val(latest.get("close"), 0.0)
    atr_val = safe_val(latest.get("atr_14"), 1.0)
    data_points = len(candles)

    if data_points < 50 or close_val <= 0 or atr_val < 1e-6:
        result = {
            "pair": pair, "signal": "HOLD", "norm_long": 0.0, "norm_short": 0.0,
            "rsi_14": 50.0, "rsi_7": 50.0, "macd_hist": 0.0, "bb_position": 0.5,
            "stoch_k": 50.0, "ema_7": 0.0, "ema_25": 0.0, "ema_50": 0.0,
            "atr_14": 1.0, "close": 0.0, "ts": 0, "kline_ts": 0,
            "data_points": data_points, "sl": 0.0, "tp": 0.0, "genome": genome,
            "error": "insufficient_data",
        }
        _result_cache[(pair, candles[-1]["ts"] if candles else 0)] = result
        return result

    rsi14_val = safe_val(latest.get("rsi_14"), 50.0)
    rsi7_val = safe_val(latest.get("rsi_7"), 50.0)
    macd_hist_val = safe_val(latest.get("macd_hist"), 0.0)
    bb_val = safe_val(latest.get("bb_position"), 0.5)
    stoch_val = safe_val(latest.get("stoch_k"), 50.0)
    ema7_val = safe_val(latest.get("ema_7"), 0.0)
    ema25_val = safe_val(latest.get("ema_25"), 0.0)
    ema50_val = safe_val(latest.get("ema_50"), 0.0)
    ts_val = safe_val(latest.get("timestamps"))

    atr_sl_mult = genome.get("atr_sl", 3.99)
    atr_tp_mult = genome.get("atr_tp", 3.61)

    sl_price = round(close_val - atr_val * atr_sl_mult, 10) if close_val else 0.0
    tp_price = round(close_val + atr_val * atr_tp_mult, 10) if close_val else 0.0

    result = {
        "pair": pair, "signal": signal,
        "norm_long": round(norm_long, 4), "norm_short": round(norm_short, 4),
        "rsi_14": round(rsi14_val, 2), "rsi_7": round(rsi7_val, 2),
        "macd_hist": round(macd_hist_val, 6), "bb_position": round(bb_val, 4),
        "stoch_k": round(stoch_val, 2),
        "ema_7": round(ema7_val, 4), "ema_25": round(ema25_val, 4),
        "ema_50": round(ema50_val, 4), "atr_14": round(atr_val, 4),
        "close": _prec(close_val), "ts": ts_val, "kline_ts": ts_val,
        "data_points": data_points, "sl": sl_price, "tp": tp_price,
        "genome": genome,
    }

    with _result_cache_lock:
        _result_cache[(pair, candles[-1]["ts"] if candles else 0)] = result
        if len(_result_cache) > 500:
            _result_cache.clear()

    return result


async def fetch_and_calculate_all(state_ref, state_lock, genome):
    tick_start = time.time()
    with state_lock:
        pairs_snapshot = list(state_ref["trading_pairs"])

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

    async def fetch_with_limit(client, pair):
        async with semaphore:
            return pair, await fetch_pair_candles(client, pair)

    async with httpx.AsyncClient(timeout=60.0, proxy=PROXY) as client:
        tasks = [fetch_with_limit(client, pair) for pair in pairs_snapshot]
        all_results = await asyncio.gather(*tasks, return_exceptions=True)

    calc_start = time.time()
    fetch_end = calc_start
    results = {}
    pair_candles = []

    for result in all_results:
        if isinstance(result, tuple) and len(result) == 2:
            pair_candles.append(result)

    valid_pairs = [(p, c) for p, c in pair_candles if isinstance(c, list) and len(c) >= 10]
    invalid_pairs = [(p, c) for p, c in pair_candles if not (isinstance(c, list) and len(c) >= 10)]

    n_workers = min(32, max(4, len(valid_pairs)))
    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        calc_results = list(executor.map(
            lambda args: calculate_for_pair(args[0], args[1], genome), valid_pairs))

    for (pair, _), result in zip(valid_pairs, calc_results):
        results[pair] = result
        if result.get("close", 0) == 0 and result.get("data_points", 0) > 0:
            results[pair]["error"] = "zero_price"

    for pair, candles in invalid_pairs:
        err_msg = "fetch_failed"
        if isinstance(candles, list):
            err_msg = f"empty_candles" if len(candles) == 0 else f"insufficient({len(candles)}<10)"
        elif isinstance(candles, Exception):
            err_msg = f"exception:{type(candles).__name__}"
        results[pair] = {"pair": pair, "signal": "HOLD", "data_points": len(candles) if isinstance(candles, list) else 0, "error": err_msg}

    calc_end = time.time()
    fetch_errors = len(invalid_pairs)
    if fetch_errors:
        logging.warning("[FETCH] %d/%d pairs failed", fetch_errors, len(pairs_snapshot))

    # Normalize indicator scores to 0-100; top 50% → LONG, rest → HOLD
    try:
        all_scores = [(p, max(r.get("norm_long", 0), r.get("norm_short", 0)))
                      for p, r in results.items() if r.get("data_points", 0) > 0]
        if all_scores:
            mn = min(v for _, v in all_scores)
            mx = max(v for _, v in all_scores)
            rng = mx - mn if mx > mn else 1.0
            sorted_by_score = sorted(all_scores, key=lambda x: -x[1])
            top_n = max(1, len(sorted_by_score) // 2)
            for i, (pair, score) in enumerate(sorted_by_score):
                if pair in results:
                    results[pair]["score"] = round((score - mn) / rng * 100, 1)
                    if i >= top_n:
                        results[pair]["signal"] = "HOLD"
    except Exception as e:
        logging.warning("[FETCH] score norm failed: %s", e)

    return results, {
        "fetch_ms": round((fetch_end - tick_start) * 1000),
        "calc_ms": round((calc_end - calc_start) * 1000),
        "total_ms": round((calc_end - tick_start) * 1000),
        "pairs": len(pairs_snapshot),
        "fetch_errors": fetch_errors,
    }


async def fetch_all_prices(state_ref, state_lock):
    with state_lock:
        pairs_snapshot = list(state_ref["trading_pairs"])
    
    prices = {}
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
    
    async def fetch_with_limit(client, pair):
        async with semaphore:
            return pair, await fetch_okx_ticker(client, pair)
    
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30.0, proxy=PROXY) as client:
                tasks = [fetch_with_limit(client, pair) for pair in pairs_snapshot]
                results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=60.0)
                for result in results:
                    if isinstance(result, tuple) and len(result) == 2:
                        pair, ticker = result
                        if isinstance(ticker, dict) and ticker:
                            prices[pair] = ticker
                if prices:
                    break
        except Exception as e:
            logging.warning(f"[PRICE_UPDATE] attempt={attempt} failed: {e}")
            if attempt == 2:
                continue
    
    missing = len(pairs_snapshot) - len(prices)
    if missing:
        logging.warning(f"[PRICE_UPDATE] {missing}/{len(pairs_snapshot)} pairs failed to fetch valid price")
    return prices


_instruments_cache = {"data": None, "timestamp": 0}
_INSTRUMENTS_CACHE_TTL = 3600


def fetch_all_usdt_swap_instruments():
    now = time.time()
    if _instruments_cache["data"] is not None and (now - _instruments_cache["timestamp"]) < _INSTRUMENTS_CACHE_TTL:
        return _instruments_cache["data"]
    from constants import OKX_BASE, PROXY, DEFAULT_INSTRUMENTS
    try:
        url = f"{OKX_BASE}/api/v5/public/instruments?instType=SWAP"
        with httpx.Client(timeout=10.0, headers={"User-Agent": "Mozilla/5.0"}, proxy=PROXY) as client:
            res = client.get(url).json()
            if res.get("code") == "0":
                instruments = [d["instId"] for d in res["data"] if d["instId"].endswith("-USDT-SWAP")]
                _instruments_cache["data"] = sorted(instruments)
                _instruments_cache["timestamp"] = now
                logging.info("[OKX] Discovered %d USDT-SWAP pairs", len(instruments))
                return instruments
    except Exception as e:
        logging.warning("[OKX] Instrument fetch failed: %s", e)
    return _instruments_cache["data"] if _instruments_cache["data"] is not None else sorted(DEFAULT_INSTRUMENTS)


def fetch_single_pair_candles(pair, retries=2):
    """Synchronous wrapper for fetch_pair_candles — for one-off route handler use."""
    import asyncio
    from constants import PROXY
    loop = asyncio.new_event_loop()
    try:
        async def _fetch():
            async with httpx.AsyncClient(timeout=60.0, proxy=PROXY) as client:
                return await fetch_pair_candles(client, pair, retries=retries)
        return loop.run_until_complete(_fetch())
    finally:
        loop.close()
