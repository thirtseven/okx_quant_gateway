"""Strategy parameter management — dynamic model-based.

Loads strategy params from active model's params.json.
Falls back to defaults if model not available.
"""
import json
import logging
import os
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

_strategy_data: Optional[Dict[str, Any]] = None
_initialized = False


def _get_active_model_params() -> Optional[Dict[str, Any]]:
    try:
        from model.manager import get_active_model
        container = get_active_model()
        if container and container.params:
            return container.params
    except Exception as e:
        logger.debug("Model manager not available: %s", e)
    return None


def init_strategy_params():
    global _strategy_data, _initialized
    if _initialized:
        return

    model_params = _get_active_model_params()
    if model_params:
        _strategy_data = model_params
        _initialized = True
        logger.info("Strategy loaded from active model: %s",
                    _strategy_data.get("version", "unknown"))
        return

    logger.warning("No active model found, using defaults")
    _strategy_data = {}
    _initialized = True


def _get_genome() -> Dict[str, Any]:
    if not _initialized:
        init_strategy_params()
    if not _strategy_data:
        return {}
    return _strategy_data.get("params", _strategy_data)


def _get_trading_params() -> Dict[str, Any]:
    sd = _strategy_data or {}
    return sd.get("trading", {})


def get_strategy_data() -> Dict[str, Any]:
    if not _initialized:
        init_strategy_params()
    return _strategy_data.copy() if _strategy_data else {}


def get_strategy_params() -> Dict[str, Any]:
    return _get_genome()


def get_param(path: str, default: Any = None) -> Any:
    if not _initialized:
        init_strategy_params()
    genome = _get_genome()
    keys = path.split(".")
    value = genome
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return default
    return value


def set_param(path: str, value: Any) -> bool:
    global _strategy_data
    if not _initialized:
        init_strategy_params()
    
    if not path or not isinstance(path, str):
        return False
    
    if '..' in path or '/' in path or '\\' in path:
        logger.error("Invalid param path: %s", path)
        return False
    
    if len(path) > 256:
        logger.error("Param path too long: %s", path)
        return False
    
    allowed_prefixes = ["regimes.", "general.", "trading.", "model."]
    if not any(path.startswith(prefix) or path == prefix.rstrip('.') for prefix in allowed_prefixes):
        if '.' in path:
            logger.error("Param path not in allowed namespace: %s", path)
            return False
    
    genome = _get_genome()
    keys = path.split(".")
    current = genome
    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value
    _save_strategy()
    logger.info("Set: %s = %s", path, value)
    return True


def reset_params() -> bool:
    global _initialized
    _initialized = False
    init_strategy_params()
    logger.info("Reset to model defaults")
    return True


def set_params(params: Dict[str, Any]) -> bool:
    if not isinstance(params, dict):
        return False
    for key, value in params.items():
        if not set_param(key, value):
            return False
    return True


def _save_strategy():
    if _strategy_data:
        try:
            from model.manager import get_active_model
            container = get_active_model()
            if container and container.path:
                params_file = os.path.join(container.path, "params.json")
                with open(params_file, "w") as f:
                    json.dump(_strategy_data, f, indent=2)
                logger.info("Saved strategy to model params: %s", params_file)
            else:
                logger.warning("No active model path, strategy not persisted")
        except Exception as e:
            logger.warning("Save failed: %s", e)


def get_regime_mode() -> str:
    genome = _get_genome()
    return genome.get("regime_mode", "auto")


def set_regime_mode(mode: str) -> bool:
    if mode not in ("auto", "bull", "bear", "range"):
        return False
    genome = _get_genome()
    genome["regime_mode"] = mode
    _save_strategy()
    logger.info("Regime: %s", mode)
    return True


def _DEFAULT_REGIMES() -> Dict[str, Any]:
    return {
        "bull":  {"score_min": 1.50, "rsi_os": 28, "rsi_ob": 76, "rsi_w": 0.82,
                  "macd_w": 0.89, "bb_w": 1.03, "bb_thresh": 0.30, "bb_hi": 0.70,
                  "stoch_w": 0.95, "stoch_os": 25, "stoch_ob": 83, "ema_w": 0.30,
                  "atr_sl": 3.65, "atr_tp": 5.51},
        "bear":  {"score_min": 1.50, "rsi_os": 28, "rsi_ob": 76, "rsi_w": 0.82,
                  "macd_w": 0.89, "bb_w": 1.03, "bb_thresh": 0.30, "bb_hi": 0.70,
                  "stoch_w": 0.95, "stoch_os": 25, "stoch_ob": 83, "ema_w": 0.30,
                  "atr_sl": 3.65, "atr_tp": 5.51},
        "range": {"score_min": 1.20, "rsi_os": 23, "rsi_ob": 77, "rsi_w": 0.60,
                  "macd_w": 0.75, "bb_w": 0.52, "bb_thresh": 0.20, "bb_hi": 0.80,
                  "stoch_w": 0.85, "stoch_os": 20, "stoch_ob": 80, "ema_w": 0.25,
                  "atr_sl": 3.00, "atr_tp": 5.00},
    }


def get_all_regimes() -> Dict[str, Any]:
    defaults = _DEFAULT_REGIMES()
    genome = _get_genome()
    file_regimes = genome.get("regimes", {})
    if not file_regimes:
        return defaults
    merged = {}
    for regime in ("bull", "bear", "range"):
        base = dict(defaults.get(regime, {}))
        base.update(file_regimes.get(regime, {}))
        merged[regime] = base
    return merged


def get_general_params() -> Dict[str, Any]:
    genome = _get_genome()
    gp = genome.get("general", {})
    if gp:
        return gp.copy()
    tp = _get_trading_params()
    return {
        "leverage": 3.0,
        "fee": tp.get("fee", 0.0015),
        "max_bars": tp.get("max_hold", 48),
        "min_atr_pct": 0.005,
        "kelly_frac": 0.5,
        "position": tp.get("position", "long_only"),
    }


def get_params_summary() -> Dict[str, Any]:
    summary = {"version": _strategy_data.get("version", _strategy_data.get("strategy_name", "unknown")),
               "regime_mode": get_regime_mode()}
    regimes = get_all_regimes()
    for r in ("bull", "bear", "range"):
        rp = regimes.get(r, {})
        summary[r] = {"score_min": rp.get("score_min", "?"),
                      "atr_sl": rp.get("atr_sl", "?"),
                      "atr_tp": rp.get("atr_tp", "?")}
    stats = _strategy_data.get("test", {})
    summary["win_rate"] = stats.get("wr", 0) * 100
    summary["sharpe"] = 0
    summary["fee"] = get_general_params().get("fee", 0.0015)
    summary["position"] = get_general_params().get("position", "long_only")
    return summary
