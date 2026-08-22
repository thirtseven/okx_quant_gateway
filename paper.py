"""模拟交易模块 (paper trading) — 跟随 BUY 信号开仓并计算得失.

设计: 虚拟资金 10000 USDT 等权分配到当前 BUY 信号币; 信号变化时调仓
(平掉不再 BUY 的币, 开新进入的币), 记录每笔平仓盈亏与浮动盈亏.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass


@dataclass
class Position:
    coin: str
    entry_price: float
    size: float          # 币数量
    entry_ts: str
    weight: float        # 分配权重


@dataclass
class ClosedTrade:
    coin: str
    entry_price: float
    exit_price: float
    size: float
    pnl: float           # 实现盈亏 (含手续费)
    return_pct: float    # 单笔收益率
    entry_ts: str
    exit_ts: str


class PaperTrader:
    INITIAL_CAPITAL: float = 10000.0
    FEE_RATE: float = 0.0015  # taker 双边手续费

    def __init__(self) -> None:
        self.initial_capital = self.INITIAL_CAPITAL
        self.cash = self.INITIAL_CAPITAL
        self.positions: dict[str, Position] = {}
        self.closed_trades: list[ClosedTrade] = []
        self._lock = threading.Lock()

    def _now(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")

    def _open(self, coin: str, price: float, capital: float) -> None:
        if price is None or price <= 0:
            return
        size = capital / price
        fee = capital * self.FEE_RATE
        self.cash -= capital + fee
        self.positions[coin] = Position(
            coin=coin,
            entry_price=price,
            size=size,
            entry_ts=self._now(),
            weight=capital / self.initial_capital,
        )

    def _close(self, coin: str, price: float | None) -> None:
        pos = self.positions.pop(coin, None)
        if pos is None:
            return
        exit_price = pos.entry_price if price is None or price <= 0 else price
        proceeds = pos.size * exit_price
        fee = proceeds * self.FEE_RATE
        self.cash += proceeds - fee
        gross = proceeds - pos.size * pos.entry_price
        entry_fee = pos.size * pos.entry_price * self.FEE_RATE
        pnl = gross - entry_fee - fee
        self.closed_trades.append(ClosedTrade(
            coin=coin,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            size=pos.size,
            pnl=pnl,
            return_pct=(exit_price / pos.entry_price - 1.0) * 100.0,
            entry_ts=pos.entry_ts,
            exit_ts=self._now(),
        ))

    def update(self, buy_coins: list[str], prices: dict[str, float]) -> None:
        """跟随最新 BUY 信号调仓. buy_coins: BUY 信号币集合."""
        with self._lock:
            target = set(buy_coins)
            for coin in list(self.positions):
                if coin not in target:
                    self._close(coin, prices.get(coin))
            per_coin = self.initial_capital / max(len(target), 1) * (1 - self.FEE_RATE)
            for coin in sorted(target):
                if coin not in self.positions:
                    self._open(coin, prices.get(coin), per_coin)

    def summary(self, prices: dict[str, float] | None = None) -> dict:
        """持仓 + 实现盈亏 + 浮动盈亏 + 总权益."""
        prices = prices or {}
        with self._lock:
            pos_out: list[dict] = []
            floating_pnl = 0.0
            position_value = 0.0
            for coin, pos in self.positions.items():
                cur = prices.get(coin, pos.entry_price)
                value = pos.size * cur
                fp = (cur - pos.entry_price) * pos.size
                floating_pnl += fp
                position_value += value
                pos_out.append({
                    "coin": coin,
                    "entry_price": round(pos.entry_price, 6),
                    "current_price": round(cur, 6),
                    "size": round(pos.size, 6),
                    "weight": round(pos.weight, 4),
                    "value": round(value, 2),
                    "pnl": round(fp, 2),
                    "pnl_pct": round((cur / pos.entry_price - 1.0) * 100.0, 4),
                    "entry_ts": pos.entry_ts,
                })
            realized_pnl = sum(t.pnl for t in self.closed_trades)
            total_equity = self.cash + position_value
            return {
                "initial_capital": self.initial_capital,
                "cash": round(self.cash, 2),
                "position_value": round(position_value, 2),
                "total_equity": round(total_equity, 2),
                "realized_pnl": round(realized_pnl, 2),
                "floating_pnl": round(floating_pnl, 2),
                "total_pnl": round(realized_pnl + floating_pnl, 2),
                "total_return_pct": round(
                    (total_equity / self.initial_capital - 1.0) * 100.0, 4
                ),
                "open_positions": pos_out,
                "closed_count": len(self.closed_trades),
                "closed_trades": [asdict(t) for t in self.closed_trades[-20:]],
            }

    def reset(self) -> dict:
        """清空全部持仓与历史成交, 恢复初始资金."""
        with self._lock:
            self.cash = self.INITIAL_CAPITAL
            self.positions = {}
            self.closed_trades = []
        return self.summary()
