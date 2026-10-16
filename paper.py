"""模拟交易模块 (paper trading) — 跟随 BUY 信号开仓并计算得失.

设计: 虚拟资金 10000 USDT 等权分配到当前 BUY 信号币; 信号变化时调仓
(平掉不再 BUY 的币, 开新进入的币), 记录每笔平仓盈亏与浮动盈亏.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from config import BASE_DIR


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
        self._state_file = BASE_DIR / "paper_state.json"
        self._load()

    def _save(self) -> None:
        """持久化仓位/成交/资金 (EXE 模式下写入 EXE 目录)."""
        try:
            payload = {
                "cash": self.cash,
                "positions": {c: asdict(p) for c, p in self.positions.items()},
                "closed_trades": [asdict(t) for t in self.closed_trades],
            }
            self._state_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> None:
        if not self._state_file.exists():
            return
        try:
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
            self.cash = float(payload.get("cash", self.INITIAL_CAPITAL))
            self.positions = {
                c: Position(**p) for c, p in (payload.get("positions") or {}).items()
            }
            self.closed_trades = [ClosedTrade(**t) for t in (payload.get("closed_trades") or [])]
        except Exception:
            pass

    def _now(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")

    def _open(self, coin: str, price: float, capital: float) -> None:
        if price is None or price <= 0:
            return
        if capital <= 0:
            return
        # capital 为含手续费的预算: 净投入 = capital/(1+fee), fee 单独计提
        net = capital / (1 + self.FEE_RATE)
        size = net / price
        fee = net * self.FEE_RATE
        self.cash -= net + fee
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
        """目标权重再平衡: 只持有 BUY 信号币, 等权分配全部资金.

        平掉不在 BUY 集合的币; 对每个 BUY 币按 总权益/币数 计算目标市值,
        超额平仓、不足补开, 确保权重始终均衡 (非增量式, 避免集合变化时权重失衡).
        """
        with self._lock:
            target = set(buy_coins)
            # 1) 平掉退出 BUY 集合的币
            for coin in list(self.positions):
                if coin not in target:
                    self._close(coin, prices.get(coin))
            if not target:
                return
            # 2) 计算总权益 (现金 + 当前持仓市值)
            equity = self.cash
            for coin, pos in self.positions.items():
                px = prices.get(coin, pos.entry_price)
                equity += pos.size * px
            # 3) 每币目标市值 (预留手续费)
            target_val = equity / len(target) / (1 + self.FEE_RATE)
            # 4) 再平衡: 超额平掉, 不足补开
            for coin in sorted(target):
                px = prices.get(coin)
                if px is None or px <= 0:
                    continue
                pos = self.positions.get(coin)
                cur_val = pos.size * px if pos else 0.0
                diff = target_val - cur_val
                if diff > 0:
                    # 补仓到目标市值
                    if pos is None:
                        self._open(coin, px, target_val)
                    else:
                        # 补仓: diff 为目标新增市值(预算已含手续费), 现金支出即 diff
                        add_size = diff / px / (1 + self.FEE_RATE)
                        self.cash -= diff
                        self.positions[coin] = Position(
                            coin=coin,
                            entry_price=px,
                            size=pos.size + add_size,
                            entry_ts=pos.entry_ts,
                            weight=target_val / self.initial_capital,
                        )
                elif diff < -1e-9:
                    # 超出目标: 平掉部分 (全额平掉交给 _close, 避免回款重复计算)
                    close_size = -diff / px
                    new_size = pos.size - close_size
                    if new_size <= 1e-12:
                        self._close(coin, px)
                    else:
                        proceeds = close_size * px
                        fee = proceeds * self.FEE_RATE
                        self.cash += proceeds - fee
                        # 记录已实现的部分盈亏, 保证 realized_pnl 与总权益一致
                        entry_fee = close_size * pos.entry_price * self.FEE_RATE
                        self.closed_trades.append(ClosedTrade(
                            coin=coin,
                            entry_price=pos.entry_price,
                            exit_price=px,
                            size=close_size,
                            pnl=proceeds - close_size * pos.entry_price - entry_fee - fee,
                            return_pct=(px / pos.entry_price - 1.0) * 100.0,
                            entry_ts=pos.entry_ts,
                            exit_ts=self._now(),
                        ))
                        self.positions[coin] = Position(
                            coin=coin,
                            entry_price=pos.entry_price,
                            size=new_size,
                            entry_ts=pos.entry_ts,
                            weight=target_val / self.initial_capital,
                        )
            self._save()

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
            self._save()
        return self.summary()
