"""Chandelier Exit strategy — long/short + position sizing.

Entry LONG (all):
  KDJ golden cross (K crosses above D) + RSI>45 + MACD bull + close>EMA50 + ADX>=25
Entry SHORT (all):
  KDJ dead cross (K crosses below D) + RSI<55 + MACD bear + close<EMA50 + ADX>=25

Exit:
  Chandelier stop (3*ATR from highest/lowest since entry) + profit-lock
  (once profit >= lock_atr*ATR, stop ratchets to max(chandelier, entry±min_lock))

Position sizing (volatility-target):
  size = risk_pct / (init_stop_atr * atr_pct)  — fixed fractional risk per trade
  Returns are leveraged by size, capped at max_leverage.

Cost: 0.3%/side fee + 0.05%/side slippage.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR: Path = Path(os.environ.get("QUANT_DATA_DIR") or str(Path(__file__).resolve().parent / ".." / "quant-local" / "data" / "okx_4h_4y_indicators"))
FEE: float = 0.003
SLIPPAGE: float = 0.0005
RSI_MIN_LONG: float = 45.0
RSI_MAX_SHORT: float = 55.0
ADX_MIN: float = 25.0
INIT_STOP_ATR: float = 2.0
CHANDELIER_MULT: float = 3.0
LOCK_ATR: float = 1.0
MIN_LOCK_ATR: float = 0.5
MAX_HOLD: int = 30
RISK_PCT: float = 0.01      # 1% account risk per trade
MAX_LEVERAGE: float = 3.0


def load_all() -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for path in sorted(DATA_DIR.glob("*.parquet")):
        coin = path.name.split("_USDT")[0]
        frames[coin] = pd.read_parquet(path)
    return frames


def backtest_coin(coin: str, df: pd.DataFrame) -> dict:
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    atr = df["atr_14"].to_numpy(dtype=float)
    atr_pct = df["atr_pct"].to_numpy(dtype=float)
    k = df["stoch_k"].to_numpy(dtype=float)
    d = df["stoch_d"].to_numpy(dtype=float)
    rsi = df["rsi_14"].to_numpy(dtype=float)
    macd_l = df["macd_line"].to_numpy(dtype=float)
    macd_s = df["macd_signal"].to_numpy(dtype=float)
    ema_50 = df["ema_50"].to_numpy(dtype=float)
    adx = df["adx_14"].to_numpy(dtype=float)

    n = len(df)
    position = 0  # -1 short, 0 flat, 1 long
    entry_price = 0.0
    extreme = 0.0
    stop = 0.0
    hold_count = 0
    trades = 0
    wins = 0
    rets: list[float] = []
    exit_reasons: list[str] = []
    equity = 1.0
    eq_curve: list[float] = []

    for i in range(1, n):
        kdj_gold = k[i - 1] <= d[i - 1] and k[i] > d[i]
        kdj_dead = k[i - 1] >= d[i - 1] and k[i] < d[i]
        if position == 0:
            long_sig = (
                kdj_gold and rsi[i] > RSI_MIN_LONG
                and macd_l[i] > macd_s[i] and close[i] > ema_50[i]
                and adx[i] >= ADX_MIN
            )
            short_sig = (
                kdj_dead and rsi[i] < RSI_MAX_SHORT
                and macd_l[i] < macd_s[i] and close[i] < ema_50[i]
                and adx[i] >= ADX_MIN
            )
            if long_sig:
                position = 1
                entry_price = close[i]
                extreme = high[i]
                stop = entry_price - INIT_STOP_ATR * atr[i]
                hold_count = 0
            elif short_sig:
                position = -1
                entry_price = close[i]
                extreme = low[i]
                stop = entry_price + INIT_STOP_ATR * atr[i]
                hold_count = 0
        else:
            hold_count += 1
            if position == 1:
                extreme = max(extreme, high[i])
                if extreme - entry_price >= LOCK_ATR * atr[i]:
                    stop = max(extreme - CHANDELIER_MULT * atr[i],
                               entry_price + MIN_LOCK_ATR * atr[i])
                hit = close[i] <= stop
            else:
                extreme = min(extreme, low[i])
                if entry_price - extreme >= LOCK_ATR * atr[i]:
                    stop = min(extreme + CHANDELIER_MULT * atr[i],
                               entry_price - MIN_LOCK_ATR * atr[i])
                hit = close[i] >= stop
            reason = "stop" if hit else ("max_hold" if hold_count >= MAX_HOLD else "")
            if reason:
                gross_ret = (close[i] / entry_price - 1.0) * position
                size = min(RISK_PCT / max(INIT_STOP_ATR * atr_pct[i], 1e-6), MAX_LEVERAGE)
                cost = 2.0 * (FEE + SLIPPAGE)
                net_ret = gross_ret * size - cost * size
                position = 0
                trades += 1
                wins += 1 if net_ret > 0 else 0
                rets.append(net_ret)
                exit_reasons.append(reason)
                equity *= (1.0 + net_ret)
        eq_curve.append(equity)

    if position != 0:
        gross_ret = (close[-1] / entry_price - 1.0) * position
        size = min(RISK_PCT / max(INIT_STOP_ATR * atr_pct[-1], 1e-6), MAX_LEVERAGE)
        net_ret = gross_ret * size - 2.0 * (FEE + SLIPPAGE) * size
        trades += 1
        wins += 1 if net_ret > 0 else 0
        rets.append(net_ret)
        exit_reasons.append("open_exit")
        equity *= (1.0 + net_ret)

    eq = np.asarray(eq_curve, dtype=float)
    peak = np.maximum.accumulate(eq)
    max_dd = float(((peak - eq) / np.maximum(peak, 1e-12)).max()) if len(eq) else 0.0
    arr = np.asarray(rets, dtype=float)
    sharpe = 0.0
    if len(arr) >= 2 and arr.std() > 1e-12:
        sharpe = float(arr.mean() / arr.std() * np.sqrt(len(arr)))

    long_trades = [r for r, x in zip(rets, ["L"] * len(rets))]
    return {
        "coin": coin,
        "trades": trades,
        "wins": wins,
        "win_rate": wins / trades if trades else 0.0,
        "total_return": equity - 1.0,
        "mean_trade": float(arr.mean()) if len(arr) else 0.0,
        "median_trade": float(np.median(arr)) if len(arr) else 0.0,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "stop_exits": exit_reasons.count("stop"),
        "max_hold_exits": exit_reasons.count("max_hold"),
        "rets": rets,
        "eq_curve": eq_curve,
    }


def main() -> None:
    t0 = time.time()
    frames = load_all()
    print(f"loaded {len(frames)} coins in {time.time()-t0:.1f}s")

    results = [backtest_coin(coin, df) for coin, df in frames.items()]
    all_rets = [r for res in results for r in res["rets"]]
    arr = np.asarray(all_rets, dtype=float)
    total_trades = sum(r["trades"] for r in results)
    total_wins = sum(r["wins"] for r in results)

    max_len = max(len(r["eq_curve"]) for r in results)
    eq_sum = np.zeros(max_len)
    counts = np.zeros(max_len)
    for r in results:
        c = np.asarray(r["eq_curve"])
        idx = np.linspace(0, len(c) - 1, max_len).astype(int)
        eq_sum += c[idx]
        counts += 1.0
    eq_avg = eq_sum / np.maximum(counts, 1e-12)
    peak = np.maximum.accumulate(eq_avg)
    port_dd = float(((peak - eq_avg) / np.maximum(peak, 1e-12)).max())

    print("\n" + "=" * 68)
    print("CHANDELIER EXIT — LONG/SHORT + POSITION SIZING (69 coins, 4h)")
    print("=" * 68)
    print(f"多: KDJ金叉+RSI>45+MACD多头+close>EMA50")
    print(f"空: KDJ死叉+RSI<55+MACD空头+close<EMA50")
    print(f"止损: 吊灯3×ATR + 锁利(LOCK {LOCK_ATR}ATR→保本+{MIN_LOCK_ATR}ATR)")
    print(f"仓位: 单笔风险 {RISK_PCT:.0%}, 波动率反比, 杠杆上限 {MAX_LEVERAGE}x")
    print(f"成本: {FEE:.3%}/边 + {SLIPPAGE:.3%}/边")
    print("-" * 68)
    print(f"交易: {total_trades} | 覆盖: {sum(1 for r in results if r['trades']>0)}/{len(results)}")
    print(f"胜率: {total_wins/max(total_trades,1):.2%}")
    print(f"单笔均: {arr.mean():.4%} | 中位: {np.median(arr):.4%}")
    print(f"组合收益: {eq_avg[-1]-1:.2%} | 组合回撤: {port_dd:.2%}")
    print(f"出场: 吊灯 {sum(r['stop_exits'] for r in results)} | 超时 {sum(r['max_hold_exits'] for r in results)}")

    ranked = sorted([r for r in results if r["trades"] > 0], key=lambda r: r["total_return"], reverse=True)
    print("\n=== TOP 8 币种 ===")
    print(f"{'币种':<10}{'交易':>5}{'胜率':>8}{'收益':>9}{'单笔均':>9}{'回撤':>8}{'夏普':>7}")
    for r in ranked[:8]:
        print(f"{r['coin']:<10}{r['trades']:>5}{r['win_rate']:>7.1%}{r['total_return']:>8.2%}{r['mean_trade']:>8.3%}{r['max_drawdown']:>7.1%}{r['sharpe']:>7.2f}")
    print("\n=== WORST 5 币种 ===")
    for r in ranked[-5:]:
        print(f"{r['coin']:<10}{r['trades']:>5}{r['win_rate']:>7.1%}{r['total_return']:>8.2%}{r['mean_trade']:>8.3%}{r['max_drawdown']:>7.1%}{r['sharpe']:>7.2f}")


if __name__ == "__main__":
    main()
