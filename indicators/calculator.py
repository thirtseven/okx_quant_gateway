"""Technical indicator calculations — pure ta library (pandas-based).

All indicators computed via the `ta` library for correctness and reliability.
No numba, no custom numpy loops — keeps the battle-tested ta implementations.
"""

import pandas as pd
import ta
from typing import Dict, List


# ═══════════════════════════════════════════════════════
# Public API — Indicator Calculations
# ═══════════════════════════════════════════════════════

def calc_ema(data, period):
    if len(data) < period:
        return []
    series = pd.Series(data, dtype=float)
    result = ta.trend.ema_indicator(series, window=period)
    return [None if pd.isna(v) else float(v) for v in result]


def calc_rsi(closes, period=14):
    series = pd.Series(closes, dtype=float)
    result = ta.momentum.rsi(series, window=period)
    return [None if pd.isna(v) else float(v) for v in result]


def calc_macd(closes, fast=12, slow=26, signal=9):
    series = pd.Series(closes, dtype=float)
    macd_line = ta.trend.macd(series, window_slow=slow, window_fast=fast)
    macd_series = pd.Series(macd_line)
    signal_line = ta.trend.ema_indicator(macd_series, window=signal)
    histogram = macd_line - signal_line
    return (
        [None if pd.isna(v) else float(v) for v in macd_line],
        [None if pd.isna(v) else float(v) for v in signal_line],
        [None if pd.isna(v) else float(v) for v in histogram],
    )


def calc_bb(closes, period=20):
    series = pd.Series(closes, dtype=float)
    bb = ta.volatility.BollingerBands(series, window=period)
    lower = bb.bollinger_lband()
    mid = bb.bollinger_mavg()
    upper = bb.bollinger_hband()
    result = []
    for i in range(len(closes)):
        l, m, u = lower.iloc[i], mid.iloc[i], upper.iloc[i]
        if pd.isna(l) or pd.isna(m) or pd.isna(u) or u == l:
            result.append(0.5)
        else:
            result.append(float((closes[i] - l) / (u - l)))
    return result


def calc_stoch(highs, lows, closes, k_period=14, d_period=3):
    high_series = pd.Series(highs, dtype=float)
    low_series = pd.Series(lows, dtype=float)
    close_series = pd.Series(closes, dtype=float)
    stoch = ta.momentum.StochasticOscillator(
        high=high_series, low=low_series, close=close_series,
        window=k_period, smooth_window=d_period
    )
    k_vals = stoch.stoch()
    d_vals = stoch.stoch_signal()
    return (
        [None if pd.isna(v) else float(v) for v in k_vals],
        [None if pd.isna(v) else float(v) for v in d_vals],
    )


def calc_atr(highs, lows, closes, period=14):
    if len(highs) < period or len(lows) < period or len(closes) < period:
        return []
    high_series = pd.Series(highs, dtype=float)
    low_series = pd.Series(lows, dtype=float)
    close_series = pd.Series(closes, dtype=float)
    result = ta.volatility.average_true_range(high_series, low_series, close_series, window=period)
    return [1.0 if pd.isna(v) or v == 0.0 else float(v) for v in result]


def calc_all_indicators(candles, pair: str = None):
    import pandas as pd
    from ta.volatility import KeltnerChannel
    
    if len(candles) < 50:
        return {}

    opens = [c["open"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    timestamps = [c["ts"] for c in candles]

    rsi14 = calc_rsi(closes, 14)
    rsi7 = calc_rsi(closes, 7)
    macd_line, signal_line, histogram = calc_macd(closes)
    bb = calc_bb(closes, 20)
    stoch_k, stoch_d = calc_stoch(highs, lows, closes, 14, 3)
    ema7 = calc_ema(closes, 7)
    ema25 = calc_ema(closes, 25)
    ema50 = calc_ema(closes, 50)
    atr14 = calc_atr(highs, lows, closes, 14)
    atr21 = calc_atr(highs, lows, closes, 21)
    
    sma50 = pd.Series(closes, dtype=float).rolling(50, min_periods=1).mean().values
    
    kc = KeltnerChannel(
        high=pd.Series(highs, dtype=float),
        low=pd.Series(lows, dtype=float),
        close=pd.Series(closes, dtype=float),
        window=20
    )
    kc_lower = kc.keltner_channel_lband().values
    
    eps = 1e-9
    close_vs_ema_50 = [c / (e + eps) if e is not None else 0.0 for c, e in zip(closes, ema50)]
    close_vs_sma_50 = [c / (s + eps) if s is not None else 0.0 for c, s in zip(closes, sma50)]

    return {
        "timestamps": timestamps,
        "rsi_14": rsi14,
        "rsi_7": rsi7,
        "macd": macd_line,
        "macd_signal": signal_line,
        "macd_hist": histogram,
        "bb_position": bb,
        "stoch_k": stoch_k,
        "stoch_d": stoch_d,
        "ema_7": [0.0 if v is None else v for v in ema7],
        "ema_25": [0.0 if v is None else v for v in ema25],
        "ema_50": [0.0 if v is None else v for v in ema50],
        "atr_14": [1.0 if v is None else v for v in atr14],
        "atr_21": [1.0 if v is None else v for v in atr21],
        "sma_50": [0.0 if v is None else v for v in sma50],
        "kc_lower": [0.0 if v is None else v for v in kc_lower],
        "close_vs_ema_50": close_vs_ema_50,
        "close_vs_sma_50": close_vs_sma_50,
        "close": closes,
        "open": opens,
    }


def get_latest_indicators(indicators):
    n = len(indicators.get("close", []))
    if n == 0:
        return {}
    idx = n - 1
    return {k: (v[idx] if isinstance(v, list) and idx < len(v) else v)
            for k, v in indicators.items()}


# ═══════════════════════════════════════════════════════
# Signal Computation
# ═══════════════════════════════════════════════════════

def compute_signal(ind, genome):
    if not ind or not ind.get("close"):
        return "HOLD", 0.0, 0.0

    close = _safe_get(ind, "close", 0)
    ema20 = _safe_get(ind, "ema_25", 0)
    ema50 = _safe_get(ind, "ema_50", 0)
    atr = _safe_get(ind, "atr_14", 0)

    if close <= 0 or ema50 <= 0 or ema20 <= 0:
        return "HOLD", 0.0, 0.0
    if atr < 1e-6:
        return "HOLD", 0.0, 0.0

    regime_mode = genome.get("regime_mode", "auto")
    if regime_mode in ("bull", "bear", "range"):
        detected_regime = regime_mode
    else:
        detected_regime = _detect_trend(ema20, ema50, {"trend": {"slope_threshold": 0.02}})

    if "regimes" in genome:
        regime_params = genome["regimes"].get(detected_regime, genome["regimes"]["bull"])
        config = _normalize_regime_params(regime_params)
    else:
        config = genome

    rsi = _safe_get(ind, "rsi_14", 50)
    macd_hist = _safe_get(ind, "macd_hist", 0)
    macd_signal = _safe_get(ind, "macd_signal", 0)
    bb = _safe_get(ind, "bb_position", 0.5)
    stoch = _safe_get(ind, "stoch_k", 50)

    long_score, short_score = _calculate_entry_scores(
        detected_regime, rsi, macd_hist, macd_signal, bb, stoch, ema20, ema50, config
    )

    signal = _determine_signal(detected_regime, long_score, short_score, config)
    return signal, long_score, short_score


def _normalize_regime_params(regime_params):
    bb_thresh = regime_params.get("bb_thresh", regime_params.get("bb_lo", 0.34))
    return {
        "rsi_ob": regime_params.get("rsi_ob", 75.55),
        "rsi_os": regime_params.get("rsi_os", 28.07),
        "rsi_weight": regime_params.get("rsi_w", 0.43),
        "extreme_bonus": 0.3,
        "macd_weight": regime_params.get("macd_w", 0.93),
        "macd_ratio": 0.5,
        "macd_signal_ratio": 0.3,
        "bb_weight": regime_params.get("bb_w", 0.52),
        "bb_thresh": bb_thresh,
        "bb_thresh_hi": regime_params.get("bb_hi", 1 - bb_thresh),
        "stoch_weight": regime_params.get("stoch_w", 0.32),
        "stoch_ob": regime_params.get("stoch_ob", 83.27),
        "stoch_os": regime_params.get("stoch_os", 25.59),
        "ema_weight": regime_params.get("ema_w", 0.13),
        "score_min": regime_params.get("score_min", 0.55),
    }


def _safe_get(data, key, default):
    import math
    val = data.get(key, default)
    if isinstance(val, list):
        v = val[-1] if val else None
        return default if v is None else v
    if val is None:
        return default
    if isinstance(val, float) and math.isnan(val):
        return default
    return val


def safe_val(val, default=None):
    import math
    if isinstance(val, list):
        v = val[-1] if val else None
        if v is None or v in (0, 0.0):
            return default
        if isinstance(v, float) and math.isnan(v):
            return default
        return v
    if val is None or val in (0, 0.0):
        return default
    if isinstance(val, float) and math.isnan(val):
        return default
    return val


def _detect_trend(ema20, ema50, config):
    if abs(ema50) < 1e-10:
        return "range"
    slope_pct = (ema20 - ema50) / ema50
    threshold = config.get("trend", {}).get("slope_threshold", 0.02)
    if slope_pct > threshold:
        return "bull"
    elif slope_pct < -threshold:
        return "bear"
    return "range"


def _calculate_entry_scores(trend, rsi, macd_hist, macd_signal, bb, stoch, ema20, ema50, config):
    entry = config
    long_score = 0.0
    short_score = 0.0

    rsi_ob = entry.get("rsi_ob", 75.55)
    rsi_os = entry.get("rsi_os", 28.07)
    rsi_w = entry.get("rsi_weight", 0.43)
    extreme_bonus = entry.get("extreme_bonus", 0.3)
    macd_w = entry.get("macd_weight", 0.93)
    macd_ratio = entry.get("macd_ratio", 0.5)
    macd_sig_ratio = entry.get("macd_signal_ratio", 0.3)
    bb_w = entry.get("bb_weight", 0.52)
    bb_thresh = entry.get("bb_thresh", 0.34)
    bb_thresh_hi = entry.get("bb_thresh_hi", 1 - bb_thresh)
    stoch_w = entry.get("stoch_weight", 0.32)
    stoch_ob = entry.get("stoch_ob", 83.27)
    stoch_os = entry.get("stoch_os", 25.59)
    ema_w = entry.get("ema_weight", 0.13)

    if rsi < rsi_os:
        long_score += rsi_w + extreme_bonus
    elif rsi < rsi_ob:
        long_score += (rsi_ob - rsi) / rsi_ob * rsi_w
    if rsi > (100 - rsi_os):
        short_score += rsi_w + extreme_bonus
    elif rsi > rsi_os:
        short_score += (rsi - rsi_ob) / (100 - rsi_ob) * rsi_w

    if macd_hist > 0:
        long_score += macd_w * macd_ratio
    if macd_hist < 0:
        short_score += macd_w * macd_ratio
    if macd_signal > 0:
        long_score += macd_w * macd_sig_ratio
    if macd_signal < 0:
        short_score += macd_w * macd_sig_ratio

    if bb < bb_thresh:
        long_score += bb_w * (bb_thresh - bb) / bb_thresh
    if bb > bb_thresh_hi:
        short_score += bb_w * (bb - bb_thresh_hi) / (1.0 - bb_thresh_hi)

    if stoch < stoch_os:
        long_score += stoch_w * (stoch_os - stoch) / stoch_os
    if stoch > stoch_ob:
        short_score += stoch_w * (stoch - stoch_ob) / (100 - stoch_ob)

    if ema20 > ema50:
        long_score += ema_w
    if ema20 < ema50:
        short_score += ema_w

    return long_score, short_score


def _determine_signal(trend, long_score, short_score, config):
    entry = config
    score_min = entry.get("score_min", 0.55)

    if trend == "bull":
        return "LONG" if long_score >= score_min else "HOLD"
    elif trend == "bear":
        return "SHORT" if short_score >= score_min else "HOLD"
    elif trend == "range":
        if long_score >= score_min and short_score >= score_min:
            return "LONG" if long_score > short_score else "SHORT"
        elif long_score >= score_min:
            return "LONG"
        elif short_score >= score_min:
            return "SHORT"
    return "HOLD"


def clear_indicator_cache():
    pass


def get_cache_stats() -> Dict:
    try:
        from core.state import state
        cache = state.get("indicator_cache", {})
        return {
            "cached_pairs": len(cache),
            "total_memory_kb": sum(len(str(v)) for v in cache.values()) // 1024
        }
    except Exception:
        return {"cached_pairs": 0, "total_memory_kb": 0}


def check_entry_confirmation(indicators, config):
    rsi = _safe_get(indicators, "rsi_14", 50)
    close = _safe_get(indicators, "close", 0)
    if close <= 0:
        return False, "invalid_close"
    if rsi > 95 or rsi < 5:
        return False, "extreme_rsi"
    signal, long_score, short_score = compute_signal(indicators, config)
    score_min = config.get("score_min", 0.55)
    if max(long_score, short_score) >= score_min:
        return True, signal
    return False, "score_below_threshold"


def calculate_position_size(capital, win_rate, avg_win, avg_loss, config):
    import math
    kelly_frac = config.get("kelly_frac", config.get("kelly_fraction", 0.3))
    if avg_loss == 0 or win_rate <= 0:
        return capital * kelly_frac * 0.5
    kelly = (win_rate * avg_win - (1 - win_rate) * abs(avg_loss)) / (avg_win * abs(avg_loss)) if abs(avg_loss * avg_win) > 0 else 0.25
    kelly = max(0, min(kelly, 1.0))
    return capital * kelly * kelly_frac


def check_exit_conditions(indicators, position, config):
    if position is None:
        return False, "no_position"
    close = _safe_get(indicators, "close", 0)
    if close <= 0:
        return False, "no_close"
    if position["direction"] == "LONG":
        if close <= position.get("sl_price", 0):
            return True, "SL"
        if close >= position.get("tp_price", float("inf")):
            return True, "TP"
    elif position["direction"] == "SHORT":
        if close >= position.get("sl_price", float("inf")):
            return True, "SL"
        if close <= position.get("tp_price", 0):
            return True, "TP"
    max_bars = config.get("max_bars", 50)
    if position.get("bars_held", 0) >= max_bars:
        return True, "MAX_BARS"
    return False, "holding"
