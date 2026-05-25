#!/usr/bin/env python3
"""
Monte Carlo Verification of Indicator Computations.

Randomly samples indicator values and verifies against independent manual
recalculation using pure numpy/pandas. Tests SMA, EMA, RSI, Bollinger,
ATR, Stochastic, OBV, and Returns.

All verifiers compute indicators from close[0:row_idx] (exclusive upper bound)
because stored indicators are shifted by 1 row for no-lookahead enforcement:
stored_indicator[t] = indicator_computed_from_data_up_to_t_minus_1.
"""

import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

IND_DIR = Path("/home/thirty/backup/okx_4h_4y_indicators")
RAW_DIR = Path("/home/thirty/backup/okx_4h_4y")
N_SAMPLES = 50
TOLERANCE = 1e-8
EPS = 1e-10


def verify_sma(df_ind, row_idx, period):
    """SMA at row_idx uses close[0:row_idx] (shifted → data up to row_idx-1)."""
    close = df_ind["close"].values
    end = row_idx  # shifted: stored value uses close[0:row_idx] = data up to row_idx-1
    start = max(0, end - period)
    expected = np.mean(close[start:end])
    actual = df_ind.iloc[row_idx][f"sma_{period}"]
    return abs(expected - actual) < TOLERANCE, expected, actual


def verify_ema(df_ind, row_idx, period):
    """EMA via pandas ewm — same algorithm as indicators_nolookahead."""
    close = df_ind["close"].values[:row_idx]
    s = pd.Series(close)
    expected = s.ewm(span=period, min_periods=period, adjust=False).mean().iloc[-1]
    actual = df_ind.iloc[row_idx][f"ema_{period}"]
    return abs(expected - actual) < TOLERANCE, expected, actual


def verify_rsi(df_ind, row_idx, period):
    """RSI via pandas — same algorithm."""
    close = df_ind["close"].values[:row_idx]
    s = pd.Series(close)
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean().iloc[-1]
    rs = avg_gain / (avg_loss + EPS)
    expected = 100 - (100 / (1 + rs))
    actual = df_ind.iloc[row_idx][f"rsi_{period}"]
    return abs(expected - actual) < TOLERANCE, expected, actual


def verify_atr(df_ind, row_idx, period=14):
    """ATR via pandas — same True Range + EWM."""
    close = df_ind["close"].values[:row_idx]
    high = df_ind["high"].values[:row_idx]
    low = df_ind["low"].values[:row_idx]
    s_close = pd.Series(close)
    s_high = pd.Series(high)
    s_low = pd.Series(low)
    prev_close = s_close.shift(1)
    tr = pd.concat([s_high - s_low, (s_high - prev_close).abs(), (s_low - prev_close).abs()], axis=1).max(axis=1)
    expected = tr.ewm(span=period, min_periods=period, adjust=False).mean().iloc[-1]
    actual = df_ind.iloc[row_idx]["atr_14"]
    return abs(expected - actual) < TOLERANCE, expected, actual


def verify_obv(df_ind, row_idx):
    """OBV deltas should match between consecutive rows."""
    close = df_ind["close"].values
    volume = df_ind["volume"].values
    if row_idx < 2:
        return True, 0, 0
    price_diff = close[row_idx - 1] - close[row_idx - 2]
    expected_delta = np.sign(price_diff) * volume[row_idx - 1]
    actual_delta = df_ind.iloc[row_idx]["obv"] - df_ind.iloc[row_idx - 1]["obv"]
    return abs(expected_delta - actual_delta) < 5.0, expected_delta, actual_delta


def verify_bb(df_ind, row_idx, period=20):
    """BB middle band = SMA(period)."""
    close = df_ind["close"].values
    start = max(0, row_idx - period)
    expected = np.mean(close[start:row_idx])
    actual = df_ind.iloc[row_idx]["bb_middle"]
    return abs(expected - actual) < TOLERANCE, expected, actual


def verify_returns(df_ind, row_idx, period):
    """Return at row_idx = pct_change from row_idx-period-1 to row_idx-1 (shifted)."""
    close = df_ind["close"].values
    if row_idx <= period:
        return True, 0, 0
    expected = (close[row_idx - 1] - close[row_idx - period - 1]) / close[row_idx - period - 1]
    actual = df_ind.iloc[row_idx][f"return_{period}"]
    return abs(expected - actual) < TOLERANCE, expected, actual


def verify_stoch(df_ind, row_idx, period=14):
    """Stochastic %K: 100*(close[t-1] - low_min[t-period:t]) / (high_max - low_min)."""
    close = df_ind["close"].values
    high = df_ind["high"].values
    low = df_ind["low"].values
    if row_idx < period:
        return True, 0, 0
    h_max = np.max(high[row_idx - period : row_idx])
    l_min = np.min(low[row_idx - period : row_idx])
    c_val = close[row_idx - 1]  # shifted: use t-1 close
    expected = 100 * (c_val - l_min) / (h_max - l_min + EPS)
    actual = df_ind.iloc[row_idx]["stoch_k"]
    return abs(expected - actual) < TOLERANCE, expected, actual


def main():
    random.seed(42)
    np.random.seed(42)

    files = sorted(IND_DIR.glob("*_indicators.parquet"))
    print(f"Indicator files: {len(files)}")
    print(f"Monte Carlo samples: {N_SAMPLES}")
    print(f"Tolerance: {TOLERANCE}")
    print()

    tests = [
        ("SMA(50)", verify_sma, {"period": 50}),
        ("EMA(25)", verify_ema, {"period": 25}),
        ("RSI(14)", verify_rsi, {"period": 14}),
        ("BB(20)", verify_bb, {"period": 20}),
        ("ATR(14)", verify_atr, {"period": 14}),
        ("OBV", verify_obv, {}),
        ("Returns(4)", verify_returns, {"period": 4}),
        ("Stoch%K", verify_stoch, {"period": 14}),
    ]

    results = {name: {"ok": 0, "fail": 0, "errors": []} for name, _, _ in tests}

    for sample_i in range(N_SAMPLES):
        # Pick random file and random row
        fp = random.choice(files)
        raw_fp = RAW_DIR / fp.name.replace("_indicators", "_4h")
        if not raw_fp.exists():
            continue

        df_ind = pd.read_parquet(fp)
        df_raw = pd.read_parquet(raw_fp)

        # Pick random row (skip first 500 for stable indicators)
        max_row = min(len(df_ind), len(df_raw)) - 1
        if max_row < 500:
            continue
        row = random.randint(500, max_row)

        pair = fp.stem.replace("_indicators", "")
        ts = df_ind.iloc[row]["timestamp"]

        for test_name, test_fn, kwargs in tests:
            try:
                ok, expected, actual = test_fn(df_ind, row, **kwargs)
                if ok:
                    results[test_name]["ok"] += 1
                else:
                    results[test_name]["fail"] += 1
                    results[test_name]["errors"].append(
                        f"  {pair} @ {ts}: expected={expected:.6f}, actual={actual:.6f}"
                    )
                    if len(results[test_name]["errors"]) <= 3:
                        print(f"FAIL {test_name}: {pair} @ {ts}")
                        print(f"  expected={expected:.6f}, actual={actual:.6f}")
            except Exception as e:
                results[test_name]["fail"] += 1
                results[test_name]["errors"].append(f"  {pair} @ {ts}: EXCEPTION {e}")

        if (sample_i + 1) % 10 == 0:
            print(f"  [{sample_i + 1}/{N_SAMPLES}] samples processed")

    # Final report
    print(f"\n{'=' * 70}")
    print(f"MONTE CARLO VERIFICATION RESULTS ({N_SAMPLES} samples)")
    print(f"{'=' * 70}")
    print(f"{'Indicator':<15} {'Pass':>8} {'Fail':>8} {'Rate':>8}")
    print("-" * 42)

    total_ok = 0
    total_fail = 0
    for test_name, test_fn, _ in tests:
        r = results[test_name]
        total_ok += r["ok"]
        total_fail += r["fail"]
        rate = r["ok"] / (r["ok"] + r["fail"]) * 100 if (r["ok"] + r["fail"]) > 0 else 0
        print(f"{test_name:<15} {r['ok']:>8} {r['fail']:>8} {rate:>7.1f}%")

    print("-" * 42)
    total_rate = total_ok / (total_ok + total_fail) * 100 if (total_ok + total_fail) > 0 else 0
    print(f"{'TOTAL':<15} {total_ok:>8} {total_fail:>8} {total_rate:>7.1f}%")

    if total_fail > 0:
        print(f"\nFAILURE DETAILS:")
        for test_name in results:
            for err in results[test_name]["errors"][:5]:
                print(err)

    if total_rate >= 99.0:
        print(f"\nVERDICT: PASS — {total_rate:.1f}% accuracy, indicators are correct")
    else:
        print(f"\nVERDICT: NEEDS REVIEW — {total_rate:.1f}% accuracy")


if __name__ == "__main__":
    main()
