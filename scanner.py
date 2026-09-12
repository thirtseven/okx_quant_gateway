"""scanner.py — 独立于模型筛选的 KDJ/MACD/RSI 超卖反转扫描器.

每 15 分钟扫描全部跟踪币种 (4H K 线), 检测:
  * KDJ (9,3,3): K/D/J 全部 < 30 (超卖区)
  * KDJ 金叉 (K 上穿 D) 发生在当前或前一根 K 线内
  * MACD DIF < 0 (运行在零轴下方低轨)
  * RSI(12) >= 30 (未跌破 30, 排除自由落体)
全部满足 -> BUY 信号 + 邮件通知.

与模型筛选完全独立: 不依赖 LightGBM, 不参与模型排名/模拟仓位.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pandas as pd

KDJ_N: int = 9
KDJ_M1: int = 3
KDJ_M2: int = 3
MACD_FAST: int = 12
MACD_SLOW: int = 26
MACD_SIGNAL: int = 9
RSI_PERIOD: int = 12
OVERSOLD: float = 30.0
CROSS_LOOKBACK: int = 1  # 金叉后 1 根 K 线以内
MIN_BARS: int = 60  # 指标预热所需最少 K 线


def compute_kdj(
    high: pd.Series, low: pd.Series, close: pd.Series,
    n: int = KDJ_N, m1: int = KDJ_M1, m2: int = KDJ_M2,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """KDJ (通达信/同花顺口径): RSV -> K=SMA(RSV,3,1) -> D=SMA(K,3,1) -> J=3K-2D.

    SMA(N,M) 递推等价于 ewm(alpha=M/N, adjust=False).
    """
    ll = low.rolling(n, min_periods=1).min()
    hh = high.rolling(n, min_periods=1).max()
    rsv = (close - ll) / (hh - ll).replace(0.0, np.nan) * 100.0
    rsv = rsv.fillna(50.0)
    k = rsv.ewm(alpha=1.0 / m1, adjust=False).mean()
    d = k.ewm(alpha=1.0 / m2, adjust=False).mean()
    j = 3.0 * k - 2.0 * d
    return k, d, j


def compute_macd(
    close: pd.Series,
    fast: int = MACD_FAST, slow: int = MACD_SLOW, signal: int = MACD_SIGNAL,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD: DIF=EMA12-EMA26, DEA=EMA(DIF,9), MACD=2*(DIF-DEA)."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd = (dif - dea) * 2.0
    return dif, dea, macd


def compute_rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """Wilder RSI(period)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi.fillna(50.0)


class Scanner:
    """独立扫描器: 每轮对全部币种跑 KDJ/MACD/RSI 条件, 输出 BUY 列表."""

    def __init__(self) -> None:
        self.enabled: bool = True
        self._lock = threading.Lock()
        self._last_scan: float | None = None
        self._signals: list[dict] = []
        self._last_error: str | None = None
        self._notified: set[str] = set()

    def scan(self, frames: dict[str, pd.DataFrame]) -> list[dict]:
        """扫描全部币种, 返回满足条件的 BUY 信号列表 (含指标明细)."""
        out: list[dict] = []
        for coin, frame in frames.items():
            if frame is None or frame.empty or len(frame) < MIN_BARS:
                continue
            if not {"open", "high", "low", "close"}.issubset(frame.columns):
                continue
            try:
                sig = self._scan_coin(coin, frame)
                if sig is not None:
                    out.append(sig)
            except Exception:
                continue
        with self._lock:
            self._last_scan = time.time()
            self._signals = out
            self._last_error = None
        return out

    def _scan_coin(self, coin: str, frame: pd.DataFrame) -> dict | None:
        close = frame["close"].astype(float)
        high = frame["high"].astype(float)
        low = frame["low"].astype(float)
        k, d, j = compute_kdj(high, low, close)
        dif, _dea, _macd = compute_macd(close)
        rsi = compute_rsi(close)

        # 条件 1: K/D/J 全部 < 30 (超卖区)
        if not (k.iloc[-1] < OVERSOLD and d.iloc[-1] < OVERSOLD and j.iloc[-1] < OVERSOLD):
            return None
        # 条件 2: KDJ 金叉 (K 上穿 D) 在当前或前一根 K 线内
        crossed = False
        for i in range(CROSS_LOOKBACK + 1):
            idx = -1 - i
            if idx - 1 < -len(k):
                break
            if k.iloc[idx] > d.iloc[idx] and k.iloc[idx - 1] <= d.iloc[idx - 1]:
                crossed = True
                break
        if not crossed:
            return None
        # 条件 3: DIF 运行在低轨 (零轴下方)
        if not (dif.iloc[-1] < 0.0):
            return None
        # 条件 4: RSI(12) 未跌破 30
        if not (rsi.iloc[-1] >= OVERSOLD):
            return None
        return {
            "coin": coin,
            "close": round(float(close.iloc[-1]), 6),
            "timestamp": str(frame["timestamp"].iloc[-1]),
            "k": round(float(k.iloc[-1]), 2),
            "d": round(float(d.iloc[-1]), 2),
            "j": round(float(j.iloc[-1]), 2),
            "dif": round(float(dif.iloc[-1]), 6),
            "rsi": round(float(rsi.iloc[-1]), 2),
        }

    def new_buys(self, signals: list[dict]) -> list[dict]:
        """返回相对上次通知新增的 BUY 币种 (去重; 币种退出后重新命中会再次通知)."""
        with self._lock:
            current = {s["coin"] for s in signals}
            fresh = [s for s in signals if s["coin"] not in self._notified]
            self._notified = current
            return fresh

    def reset_notified(self) -> None:
        with self._lock:
            self._notified = set()

    def summary(self) -> dict:
        with self._lock:
            return {
                "enabled": self.enabled,
                "last_scan": self._last_scan,
                "last_error": self._last_error,
                "signals": list(self._signals),
            }