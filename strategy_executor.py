"""
🧬 Genetic Crypto v274 — Local Execution Script (CPU-Compatible)
无需GPU，仅需 pip install lightgbm pandas numpy scikit-learn scipy
"""

import numpy as np, pandas as pd
from sklearn.preprocessing import RobustScaler
from scipy.stats import pearsonr
import lightgbm as lgb
from pathlib import Path

# ============================================================
# 策略参数 (from v274 convergence)
# ============================================================
STRATEGY_PARAMS = {
    "model": {
        "n_estimators": 30,
        "max_depth": 2,
        "num_leaves": 6,
        "learning_rate": 0.03,
        "reg_alpha": 1.0,
        "reg_lambda": 1.0,
        "subsample": 0.7,
        "colsample_bytree": 0.7,
        "min_child_samples": 100,
    },
    "trading": {
        "fee": 0.0015,
        "min_hold_bars": 2,
        "max_hold_bars": 48,
        "entry_threshold": 0.001,
        "exit_threshold": -0.001,
    },
    # 47 consensus features from v274
    "features": [
        "open", "high", "low", "close", "volume",
        "sma_7", "sma_14", "sma_25", "sma_50", "sma_200",
        "ema_7", "ema_14", "ema_25", "ema_50", "ema_200",
        "close_vs_sma_7", "close_vs_sma_25", "close_vs_sma_50",
        "close_vs_ema_7", "close_vs_ema_25", "close_vs_ema_50",
        "ema_7_25_ratio", "ema_25_50_ratio", "ema_50_200_ratio",
        "bb_width", "bb_middle", "bb_upper", "bb_lower",
        "atr_14", "stoch_k", "stoch_d", "rsi_14", "mfi_14",
        "volume_ratio_20", "volume_ratio_50",
        "log_return_1", "log_return_4", "log_return_8",
        "return_1", "return_4", "return_8",
        "volatility_8", "volatility_20",
        "kc_upper", "kc_middle",
        "close_position", "high_low_ratio",
    ],
}

def train_model(df_train, target_col="target_ret_1", price_col="close"):
    """Train the v274 strategy on given data"""
    feats = [f for f in STRATEGY_PARAMS["features"] if f in df_train.columns]
    print(f"📋 Using {len(feats)}/{len(STRATEGY_PARAMS['features'])} features")
    
    X = df_train[feats].values.astype(np.float32)
    y = df_train[target_col].values.astype(np.float32)
    
    # Clean
    mask = ~np.isnan(y)
    X, y = X[mask], y[mask]
    for j in range(X.shape[1]):
        nm = np.isnan(X[:, j])
        if nm.any(): X[nm, j] = np.nanmedian(X[:, j])
    
    # Scale
    scaler = RobustScaler()
    X_s = scaler.fit_transform(X)
    
    # Train (CPU mode)
    model = lgb.LGBMRegressor(
        n_estimators=100,
        max_depth=6,
        num_leaves=31,
        learning_rate=0.03,
        reg_alpha=1.0,
        reg_lambda=1.0,
        subsample=0.7,
        colsample_bytree=0.7,
        min_child_samples=50,
        random_state=42, verbose=-1,
        device_type="cpu",
    )
    model.fit(X_s, y)
    print(f"✅ Model trained on {len(X):,} samples")
    return model, scaler, feats


def predict(model, scaler, feats, df_new, price_col="close"):
    """Make predictions on new data"""
    X = df_new[feats].values.astype(np.float32)
    for j in range(X.shape[1]):
        nm = np.isnan(X[:, j])
        if nm.any(): X[nm, j] = np.nanmedian(X[:, j])
    X_s = scaler.transform(X)
    return model.predict(X_s)


def simulate_trading(predictions, prices, params=None):
    """Simulate trading with v274 strategy"""
    if params is None:
        params = STRATEGY_PARAMS["trading"]
    
    pos = 0; ep = 0; eb = 0
    trades = []; log_ret = 0.0; equity = [0.0]
    
    for i in range(min(len(predictions), len(prices))):
        sig = predictions[i]; pr = prices[i]
        
        if pos == 0 and sig > params["entry_threshold"]:
            pos = 1; ep = pr; eb = i
        elif pos == 1:
            hb = i - eb
            if sig < params["exit_threshold"] or hb >= params["max_hold_bars"]:
                if hb >= params["min_hold_bars"]:  # Min hold
                    pnl = np.log(pr / ep) - 2 * params["fee"]
                    log_ret += pnl
                    trades.append({"entry_bar": eb, "exit_bar": i,
                                   "pnl": pnl, "hold_bars": hb})
                pos = 0
        
        equity.append(log_ret)
    
    if pos == 1:
        pnl = np.log(prices[-1] / ep) - 2 * params["fee"]
        log_ret += pnl
        trades.append({"entry_bar": eb, "exit_bar": len(prices) - 1,
                       "pnl": pnl, "hold_bars": len(prices) - 1 - eb})
    
    return trades, equity, log_ret


# ============================================================
# Example usage
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("🧬 Genetic Crypto v274 — Local Execution")
    print("=" * 60)
    print(f"Required: pip install lightgbm pandas numpy scikit-learn scipy")
    print(f"GPU: NOT required (CPU mode)")
    print(f"")
    print(f"Usage:")
    print(f"  model, scaler, feats = train_model(df_train)")
    print(f"  preds = predict(model, scaler, feats, df_new)")
    print(f"  trades, equity, pnl = simulate_trading(preds, prices)")
    print(f"")
    print(f"Model params: {STRATEGY_PARAMS['model']}")
    print(f"Trading params: {STRATEGY_PARAMS['trading']}")
