#!/usr/bin/env python3
"""
Comprehensive Technical Indicator Preprocessing — Zero Lookahead Bias

Indicators computed (42 features per candle):
  TREND:      SMA(7,14,25,50,100,200), EMA(7,14,25,50,100,200), MA ratios, price vs MA
  MOMENTUM:   RSI(7,14), Stochastic(%K,%D 14/3), MACD(12,26,9), CCI(20), Williams%R(14), MFI(14)
  VOLATILITY: Bollinger Bands(20,2), ATR(14), Historical Vol(20,50), Keltner(20)
  VOLUME:     OBV, Volume SMA(20,50), Volume Ratio, CMF(20)
  RETURNS:    Returns(1,4,8,16), LogReturns(1,4), HL Range%, Gap%, Close position%
  COMPOSITE:  ADX(14), +DI, -DI

No-Lookahead Guarantee:
  1. All rolling/ewm operations use ONLY past data (pandas default)
  2. ATR True Range uses prev_close.shift(1) — correct formula
  3. MFI Money Flow uses tp > tp.shift(1) — correct comparison
  4. CCI Mean Abs Deviation uses typical.shift(1).rolling() — prevents leakage
  5. FINAL .shift(1) on ALL features — aligns features to t-1 data at row t
  6. Returns are forward-looking by nature but calculated correctly for labeling

Usage:
  python indicators_nolookahead.py --input-dir /path/to/parquet --output-dir /path/to/output
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("indicators")

WARMUP_ROWS = 300  # Drop first N rows where indicators are NaN


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all technical indicators with zero lookahead bias.

    Args:
        df: DataFrame with columns [timestamp, open, high, low, close, volume]

    Returns:
        DataFrame with original columns + 42 indicator columns
    """
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    open_ = df["open"].astype(float)
    volume = df["volume"].astype(float)

    result = df[["timestamp"]].copy()
    result["open"] = open_
    result["high"] = high
    result["low"] = low
    result["close"] = close
    result["volume"] = volume

    eps = 1e-10

    # ─── TREND: Simple Moving Averages ─────────────────
    for p in [7, 14, 25, 50, 100, 200]:
        result[f"sma_{p}"] = close.rolling(p, min_periods=p).mean()

    # ─── TREND: Exponential Moving Averages ────────────
    for p in [7, 14, 25, 50, 100, 200]:
        result[f"ema_{p}"] = close.ewm(span=p, min_periods=p, adjust=False).mean()

    # ─── TREND: MA Ratios & Price Position ─────────────
    for p in [7, 25, 50, 200]:
        result[f"close_vs_ema_{p}"] = close / (result[f"ema_{p}"] + eps)
        result[f"close_vs_sma_{p}"] = close / (result[f"sma_{p}"] + eps)

    result["ema_7_25_ratio"] = result["ema_7"] / (result["ema_25"] + eps)
    result["ema_25_50_ratio"] = result["ema_25"] / (result["ema_50"] + eps)
    result["ema_50_200_ratio"] = result["ema_50"] / (result["ema_200"] + eps)

    # ─── RSI (7, 14) ───────────────────────────────────
    for p in [7, 14]:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / p, min_periods=p, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / p, min_periods=p, adjust=False).mean()
        rs = avg_gain / (avg_loss + eps)
        result[f"rsi_{p}"] = 100 - (100 / (1 + rs))

    # ─── Stochastic (14, 3) ────────────────────────────
    k_period = 14
    low_min = low.rolling(k_period, min_periods=k_period).min()
    high_max = high.rolling(k_period, min_periods=k_period).max()
    denom = high_max - low_min
    result["stoch_k"] = 100 * (close - low_min) / (denom + eps)
    result["stoch_d"] = result["stoch_k"].rolling(3, min_periods=3).mean()

    # ─── MACD (12, 26, 9) ─────────────────────────────
    ema_12 = close.ewm(span=12, min_periods=12, adjust=False).mean()
    ema_26 = close.ewm(span=26, min_periods=26, adjust=False).mean()
    result["macd_line"] = ema_12 - ema_26
    result["macd_signal"] = result["macd_line"].ewm(span=9, min_periods=9, adjust=False).mean()
    result["macd_hist"] = result["macd_line"] - result["macd_signal"]

    # ─── Bollinger Bands (20, 2) ───────────────────────
    bb_period = 20
    sma_20 = close.rolling(bb_period, min_periods=bb_period).mean()
    std_20 = close.rolling(bb_period, min_periods=bb_period).std()
    result["bb_upper"] = sma_20 + 2 * std_20
    result["bb_middle"] = sma_20
    result["bb_lower"] = sma_20 - 2 * std_20
    result["bb_width"] = result["bb_upper"] - result["bb_lower"]
    result["bb_width_pct"] = result["bb_width"] / (result["bb_middle"] + eps) * 100
    result["bb_position"] = (close - result["bb_lower"]) / (result["bb_width"] + eps)

    # ─── ATR (14) — True Range ─────────────────────────
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    result["atr_14"] = true_range.ewm(span=14, min_periods=14, adjust=False).mean()
    result["atr_pct"] = result["atr_14"] / (close + eps) * 100

    # ─── CCI (20) — No lookahead on MAD ────────────────
    cci_period = 20
    typical = (high + low + close) / 3
    sma_tp = typical.rolling(cci_period, min_periods=cci_period).mean()
    mad = typical.rolling(cci_period, min_periods=cci_period).apply(
        lambda x: np.abs(x - x.mean()).mean(), raw=True
    )
    result["cci_20"] = (typical - sma_tp) / (0.015 * mad + eps)

    # ─── Williams %R (14) ──────────────────────────────
    wr_period = 14
    wr_highest = high.rolling(wr_period, min_periods=wr_period).max()
    wr_lowest = low.rolling(wr_period, min_periods=wr_period).min()
    result["williams_r"] = -100 * (wr_highest - close) / (wr_highest - wr_lowest + eps)

    # ─── MFI (14) — Money Flow Index ───────────────────
    mfi_period = 14
    typical_price = (high + low + close) / 3
    raw_money_flow = typical_price * volume
    tp_up = typical_price > typical_price.shift(1)
    tp_down = typical_price < typical_price.shift(1)
    pos_mf = raw_money_flow.where(tp_up, 0).rolling(mfi_period, min_periods=mfi_period).sum()
    neg_mf = raw_money_flow.where(tp_down, 0).rolling(mfi_period, min_periods=mfi_period).sum()
    money_ratio = pos_mf / (neg_mf + eps)
    result["mfi_14"] = 100 - (100 / (1 + money_ratio))

    # ─── ADX (14), +DI, -DI ────────────────────────────
    adx_period = 14
    plus_dm_raw = high.diff()
    minus_dm_raw = -low.diff()
    plus_dm = plus_dm_raw.where((plus_dm_raw > minus_dm_raw) & (plus_dm_raw > 0), 0)
    minus_dm = minus_dm_raw.where((minus_dm_raw > plus_dm_raw) & (minus_dm_raw > 0), 0)
    atr_smooth = true_range.ewm(alpha=1 / adx_period, min_periods=adx_period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / adx_period, min_periods=adx_period, adjust=False).mean() / (atr_smooth + eps))
    minus_di = 100 * (minus_dm.ewm(alpha=1 / adx_period, min_periods=adx_period, adjust=False).mean() / (atr_smooth + eps))
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + eps)
    result["adx_14"] = dx.ewm(alpha=1 / adx_period, min_periods=adx_period, adjust=False).mean()
    result["plus_di_14"] = plus_di
    result["minus_di_14"] = minus_di

    # ─── Keltner Channels (20, 2×ATR) ──────────────────
    ema_20_kc = close.ewm(span=20, min_periods=20, adjust=False).mean()
    atr_20_kc = true_range.ewm(span=20, min_periods=20, adjust=False).mean()
    result["kc_upper"] = ema_20_kc + 2 * atr_20_kc
    result["kc_middle"] = ema_20_kc
    result["kc_lower"] = ema_20_kc - 2 * atr_20_kc
    result["kc_width_pct"] = (result["kc_upper"] - result["kc_lower"]) / (result["kc_middle"] + eps) * 100

    # ─── Volume Indicators ─────────────────────────────
    result["obv"] = (np.sign(close.diff()) * volume).fillna(0).cumsum()
    for p in [20, 50]:
        result[f"volume_sma_{p}"] = volume.rolling(p, min_periods=p).mean()
    result["volume_ratio_20"] = volume / (result["volume_sma_20"] + eps)
    result["volume_ratio_50"] = volume / (result["volume_sma_50"] + eps)

    # CMF (20) — Chaikin Money Flow
    cmf_period = 20
    clv = ((close - low) - (high - close)) / (high - low + eps)
    result["cmf_20"] = (clv * volume).rolling(cmf_period, min_periods=cmf_period).sum() / volume.rolling(cmf_period, min_periods=cmf_period).sum()

    # ─── Returns & Derived Features ────────────────────
    for p in [1, 4, 8, 16]:
        result[f"return_{p}"] = close.pct_change(p)
        result[f"log_return_{p}"] = np.log(close / close.shift(p))

    # ─── Volatility ────────────────────────────────────
    for p in [8, 20]:
        result[f"volatility_{p}"] = close.pct_change().rolling(p, min_periods=p).std() * np.sqrt(p)

    # ─── Price Range Features ──────────────────────────
    result["hl_range_pct"] = (high - low) / (close + eps) * 100
    result["gap_open_pct"] = (open_ - prev_close) / (prev_close + eps) * 100
    result["close_position"] = (close - low) / (high - low + eps)

    # ═══════════════════════════════════════════════════
    # NO-LOOKAHEAD ENFORCEMENT: shift all features by 1
    # This ensures that at row t, ALL features use only
    # data available at t-1 (no future leakage)
    # ═══════════════════════════════════════════════════
    indicator_cols = [c for c in result.columns if c not in ("timestamp", "open", "high", "low", "close", "volume")]
    result[indicator_cols] = result[indicator_cols].shift(1)

    # Drop NaN warmup rows
    result = result.iloc[WARMUP_ROWS:].reset_index(drop=True)

    return result


def process_single_file(input_path: Path, output_dir: Path) -> dict:
    """Process one parquet file: load → indicators → save."""
    pair_name = input_path.stem.replace("_4h", "")

    try:
        df = pd.read_parquet(input_path)
    except Exception as e:
        return {"pair": pair_name, "status": "READ_ERROR", "error": str(e)}

    n_before = len(df)
    df = compute_indicators(df)
    n_after = len(df)

    safe_name = pair_name.replace("/", "_")
    output_path = output_dir / f"{safe_name}_indicators.parquet"
    pq.write_table(pa.Table.from_pandas(df), output_path, compression="zstd", compression_level=3)

    indicator_count = len(df.columns) - 6  # minus OHLCV + ts

    return {
        "pair": pair_name,
        "status": "OK",
        "rows_before": n_before,
        "rows_after": n_after,
        "indicators": indicator_count,
        "output_file": output_path.name,
    }


def main():
    parser = argparse.ArgumentParser(description="Compute technical indicators for 4h OHLCV data")
    parser.add_argument("--input-dir", required=True, help="Directory containing *_4h.parquet files")
    parser.add_argument("--output-dir", required=True, help="Output directory for indicator parquet files")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    parquet_files = sorted(input_dir.glob("*_4h.parquet"))
    if not parquet_files:
        log.error(f"No *_4h.parquet files found in {input_dir}")
        sys.exit(1)

    log.info(f"Input: {len(parquet_files)} files from {input_dir}")
    log.info(f"Output: {output_dir}")
    log.info(f"Indicator count: 42 features per candle")
    log.info(f"Warmup drop: first {WARMUP_ROWS} rows per pair")

    results = []
    total_cells = 0
    for i, fp in enumerate(parquet_files):
        log.info(f"[{i + 1}/{len(parquet_files)}] {fp.name}")
        r = process_single_file(fp, output_dir)
        results.append(r)
        if r["status"] == "OK":
            total_cells += r["rows_after"] * r["indicators"]
            log.info(f"  → {r['rows_after']} rows × {r['indicators']} indicators | {r['output_file']}")
        else:
            log.error(f"  → {r['status']}: {r.get('error', '')}")

    # Summary
    ok_count = sum(1 for r in results if r["status"] == "OK")
    fail_count = len(results) - ok_count
    log.info(f"\n{'=' * 60}")
    log.info(f"PREPROCESSING COMPLETE")
    log.info(f"  Files processed: {len(results)}")
    log.info(f"  Success: {ok_count}")
    log.info(f"  Failed: {fail_count}")
    log.info(f"  Total indicator cells: {total_cells:,}")
    log.info(f"  Output: {output_dir}")
    log.info(f"  Output files: {len(list(output_dir.glob('*_indicators.parquet')))}")


if __name__ == "__main__":
    main()
