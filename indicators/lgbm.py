"""LightGBM Strategy Bridge - Signal computation engine.

Delegates to model_manager for model lifecycle.
Features built via ta library indicators. This is the ONLY signal path.
"""
import json
import logging
import os
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

_model = None
_preprocessor = None
_params = None
_features = None

MODEL_FILE = os.path.join(DATA_DIR, "v274_model.txt")
PREPROC_FILE = os.path.join(DATA_DIR, "v274_preprocessor.joblib")
PARAMS_FILE = os.path.join(DATA_DIR, "v274_params.json")


def _load_model():
    global _model, _preprocessor, _params, _features
    if _model is not None:
        return True
    
    try:
        from model.manager import get_active_model
        container = get_active_model()
        if container and container.loaded:
            _model = container.model
            _preprocessor = container.preprocessor
            _params = container.params
            _features = container.features
            return True
    except Exception as e:
        logger.debug("[v274] Model manager not available, falling back to direct load: %s", e)
    
    try:
        import lightgbm as lgb
        _model = lgb.Booster(model_file=MODEL_FILE)
        _params = json.load(open(PARAMS_FILE))
        features_raw = _params["features"]
        if isinstance(features_raw, dict):
            _features = features_raw["names"]
        else:
            _features = features_raw
        n_feat = len(_features)
        champion = _params.get("champion", "v315-ga")
        logger.info("[v274] Loaded: %d features, %s", n_feat, champion)
        return True
    except Exception as e:
        logger.error("[v274] Load failed: %s", e)
        return False


def is_loaded():
    return _model is not None


def get_params():
    _load_model()
    return _params


def _build_features(df):
    if len(df) < 50:
        return None
    close = df["close"].values.astype(np.float64)
    high = df["high"].values.astype(np.float64)
    low = df["low"].values.astype(np.float64)
    vol = df.get("vol", df.get("volume", pd.Series([0]*len(df)))).values.astype(np.float64)
    open_p = df["open"].values.astype(np.float64)
    n = len(df)
    result = {"open": open_p, "high": high, "low": low, "close": close, "volume": vol}
    s_close = pd.Series(close)
    for w in [7, 14, 25, 50, 100, 200]:
        result[f"sma_{w}"] = s_close.rolling(w, min_periods=1).mean().values
        result[f"ema_{w}"] = s_close.ewm(span=w, adjust=False).mean().values
    result["rsi_14"] = _calc_rsi(close, 14)
    result["rsi_7"] = _calc_rsi(close, 7)
    result["atr_14"] = _calc_atr(high, low, close, 14)
    result["stoch_k"] = _calc_stoch(high, low, close, 14)
    result["stoch_d"] = pd.Series(result["stoch_k"]).rolling(3, min_periods=1).mean().values
    ema12 = pd.Series(close).ewm(span=12, adjust=False).mean().values
    ema26 = pd.Series(close).ewm(span=26, adjust=False).mean().values
    result["macd_signal"] = pd.Series(ema12 - ema26).ewm(span=9, adjust=False).mean().values
    eps = 1e-9
    sma7, sma25, sma50, sma200 = result["sma_7"], result["sma_25"], result["sma_50"], result["sma_200"]
    ema7, ema25, ema50, ema200 = result["ema_7"], result["ema_25"], result["ema_50"], result["ema_200"]
    result["close_vs_sma_7"] = close / (sma7 + eps)
    result["close_vs_sma_25"] = close / (sma25 + eps)
    result["close_vs_sma_50"] = close / (sma50 + eps)
    result["close_vs_sma_200"] = close / (sma200 + eps)
    result["close_vs_ema_7"] = close / (ema7 + eps)
    result["close_vs_ema_25"] = close / (ema25 + eps)
    result["close_vs_ema_50"] = close / (ema50 + eps)
    result["close_vs_ema_200"] = close / (ema200 + eps)
    result["ema_7_25_ratio"] = ema7 / (ema25 + eps)
    result["ema_25_50_ratio"] = ema25 / (ema50 + eps)
    result["ema_50_200_ratio"] = ema50 / (ema200 + eps)
    from ta.volatility import BollingerBands
    bb = BollingerBands(s_close, window=20)
    result["bb_upper"] = bb.bollinger_hband().values
    result["bb_middle"] = bb.bollinger_mavg().values
    result["bb_lower"] = bb.bollinger_lband().values
    result["bb_width"] = (result["bb_upper"] - result["bb_lower"]) / (result["bb_middle"] + eps)
    result["volume_ratio_20"] = pd.Series(vol).rolling(20, min_periods=1).mean().values
    result["volume_ratio_50"] = pd.Series(vol).rolling(50, min_periods=1).mean().values
    result["log_return_1"] = np.log(close + eps) - np.log(np.roll(close, 1) + eps)
    result["log_return_4"] = np.log(close + eps) - np.log(np.roll(close, 4) + eps)
    result["log_return_8"] = np.log(close + eps) - np.log(np.roll(close, 8) + eps)
    result["return_1"] = s_close.pct_change(1).values
    result["return_4"] = s_close.pct_change(4).values
    result["return_8"] = s_close.pct_change(8).values
    result["volatility_8"] = s_close.pct_change().rolling(8, min_periods=1).std().values
    result["volatility_20"] = s_close.pct_change().rolling(20, min_periods=1).std().values
    from ta.volatility import KeltnerChannel
    kc = KeltnerChannel(high=pd.Series(high), low=pd.Series(low), close=s_close, window=20)
    result["kc_upper"] = kc.keltner_channel_hband().values
    result["kc_middle"] = kc.keltner_channel_mband().values
    hi25 = s_close.rolling(25, min_periods=1).max().values
    lo25 = s_close.rolling(25, min_periods=1).min().values
    result["close_position"] = (close - lo25) / (hi25 - lo25 + eps)
    result["high_low_ratio"] = np.where(low > 0, high / low, 1.0)
    result["mfi_14"] = _calc_mfi(high, low, close, vol, 14)
    result["cci_20"] = _calc_cci(high, low, close, 20)
    result["kc_width_pct"] = (kc.keltner_channel_hband().values - kc.keltner_channel_lband().values) / (result["kc_middle"] + eps)
    result["obv"] = _calc_obv(close, vol)
    result["volume_sma_20"] = pd.Series(vol).rolling(20, min_periods=1).mean().values
    result["volume_sma_50"] = pd.Series(vol).rolling(50, min_periods=1).mean().values
    result["cmf_20"] = _calc_cmf(high, low, close, vol, 20)
    pc = np.roll(close, 1); pc[0] = close[0]
    result["gap_open_pct"] = (open_p - pc) / (pc + eps)
    feature_df = pd.DataFrame(result)
    feature_df = feature_df.fillna(0).replace([np.inf, -np.inf], 0)
    return feature_df


def _calc_rsi(closes, period=14):
    delta = np.diff(closes, prepend=closes[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).ewm(alpha=1/period, adjust=False).mean().values
    avg_loss = pd.Series(loss).ewm(alpha=1/period, adjust=False).mean().values
    rs = np.divide(avg_gain, avg_loss, out=np.zeros_like(avg_gain), where=avg_loss != 0)
    return 100 - 100 / (1 + rs)


def _calc_atr(high, low, close, period=14):
    h, l, c = np.array(high), np.array(low), np.array(close)
    tr = np.maximum(h - l, np.maximum(abs(h - np.roll(c, 1)), abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    return pd.Series(tr).ewm(alpha=1/period, adjust=False).mean().values


def _calc_stoch(high, low, close, period=14):
    h, l, c = np.array(high), np.array(low), np.array(close)
    n = len(c)
    k = np.zeros(n)
    for i in range(period, n):
        hh = h[i-period:i].max()
        ll = l[i-period:i].min()
        k[i] = (c[i] - ll) / (hh - ll + 1e-9) * 100
    return k


def _calc_mfi(high, low, close, vol, period=14):
    typical = (np.array(high) + np.array(low) + np.array(close)) / 3
    raw_mf = typical * np.array(vol)
    pos_flow = np.zeros(len(close))
    neg_flow = np.zeros(len(close))
    for i in range(1, len(close)):
        if typical[i] > typical[i-1]:
            pos_flow[i] = raw_mf[i]
        else:
            neg_flow[i] = raw_mf[i]
    pos_sum = pd.Series(pos_flow).rolling(period, min_periods=1).sum().values
    neg_sum = pd.Series(neg_flow).rolling(period, min_periods=1).sum().values
    return 100 - 100 / (1 + np.divide(pos_sum, neg_sum, out=np.ones_like(pos_sum), where=neg_sum != 0))


def _calc_cci(high, low, close, period=20):
    tp = (np.array(high) + np.array(low) + np.array(close)) / 3
    sma_tp = pd.Series(tp).rolling(period, min_periods=1).mean().values
    mad = pd.Series(np.abs(tp - sma_tp)).rolling(period, min_periods=1).mean().values
    return (tp - sma_tp) / (0.015 * mad + 1e-9)


def _calc_obv(close, vol):
    obv = np.zeros(len(close))
    v = np.array(vol)
    for i in range(1, len(close)):
        if close[i] > close[i-1]:
            obv[i] = obv[i-1] + v[i]
        elif close[i] < close[i-1]:
            obv[i] = obv[i-1] - v[i]
        else:
            obv[i] = obv[i-1]
    return obv


def _calc_cmf(high, low, close, vol, period=20):
    h, l, c = np.array(high), np.array(low), np.array(close)
    mfm = ((c - l) - (h - c)) / (h - l + 1e-9)
    mfv = mfm * np.array(vol)
    return pd.Series(mfv).rolling(period, min_periods=1).sum().values / (
        pd.Series(np.array(vol)).rolling(period, min_periods=1).sum().values + 1e-9)


def compute_signal(candles):
    """Single-pair prediction - returns raw score."""
    if not _load_model():
        return "HOLD", 0.0, 0.0
    if len(candles) < 50:
        return "HOLD", 0.0, 0.0
    df = pd.DataFrame(candles[-300:])
    fb = _build_features(df)
    if fb is None or len(fb) == 0:
        return "HOLD", 0.0, 0.0
    avail = [f for f in _features if f in fb.columns]
    if len(avail) < 10:
        return "HOLD", 0.0, 0.0
    X = fb[avail].values.astype(np.float32)
    for j in range(X.shape[1]):
        mask = np.isnan(X[:, j])
        if mask.any():
            X[mask, j] = np.nanmedian(X[:, j])
    try:
        if _preprocessor is not None:
            scaler = _preprocessor if hasattr(_preprocessor, "transform") else _preprocessor.get("scaler", None)
            if scaler is not None:
                X = scaler.transform(X)
        raw = float(_model.predict(X, num_iteration=_model.best_iteration)[-1])
        return "HOLD", raw, 0.0
    except Exception as e:
        logger.error("[v274] predict error: %s", e)
        return "HOLD", 0.0, 0.0


def compute_batch_signals(pairs_candles):
    """Batch: predict all pairs, normalize scores, rank top-N as LONG."""
    if not _load_model():
        return {}
    raw_scores = {}
    for pair, candles in pairs_candles.items():
        _, nl, _ = compute_signal(candles)
        raw_scores[pair] = nl
    scores = list(raw_scores.values())
    if not scores:
        return {}
    mn, mx = min(scores), max(scores)
    rng = mx - mn if mx > mn else 1.0
    results = {}
    sorted_pairs = sorted(raw_scores.items(), key=lambda x: -x[1])
    top_n = max(1, len(sorted_pairs) // 2)
    for i, (pair, raw) in enumerate(sorted_pairs):
        normalized = round((raw - mn) / rng * 100, 1)
        signal = "LONG" if i < top_n else "HOLD"
        if raw <= 0 and i >= top_n:
            signal = "SHORT"
        results[pair] = {"signal": signal, "norm_long": normalized, "norm_short": 0.0, "raw": raw}
    return results
