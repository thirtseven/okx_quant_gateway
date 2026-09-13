from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

_PROJ_ROOT: Path = Path(__file__).resolve().parent
V18_SOURCE: Path = Path(os.environ.get("QUANT_V18_SOURCE") or str(_PROJ_ROOT / ".." / "quant-local" / "short_term_ta_ga_local.py"))
BEST_JSON: Path = Path(os.environ.get("QUANT_BEST_JSON") or str(_PROJ_ROOT / "models" / "best_chromosome.json"))
MODEL_FILE: Path = Path(os.environ.get("QUANT_MODEL_FILE") or str(_PROJ_ROOT / "models" / "short_term_ta_ga_model.txt"))

# Momentum signal window (bars). Ranking prefers live V18 LightGBM model
# probabilities; 3-bar momentum is only the fallback when the model is
# unavailable (see rank_signal).
MOMENTUM_WINDOW: int = 3


def _load_v18() -> object | None:
    if not V18_SOURCE.exists():
        return None
    spec = importlib.util.spec_from_file_location("v18", V18_SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["v18"] = module
    try:
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


_V18 = _load_v18()


class SignalEngine:
    """Computes trading signals from candle history.

    V18 genes are loaded when available; the live LightGBM booster is fitted
    once at startup and ranking prefers real model probabilities over the
    3-bar momentum fallback.
    """

    TRAIN_COINS: int = 44  # coins used to fit the live model (same as GA training)
    FEATURE_HISTORY: int = 250  # bars fed to realtime feature computation (>= max window 48 + margin)
    # Frozen training universe in lexicographic order — MUST match the model's
    # categorical coin levels (derived from sorted(train["coin"].unique())).
    _COIN_CATEGORIES: tuple[str, ...] = (
        "AAVE", "ADA", "ALGO", "APT", "ARB", "ATOM", "AVAX", "BCH",
        "BONK", "BTC", "CRV", "DOGE", "DOT", "DYDX", "ENS", "ETC",
        "ETH", "FIL", "FLOKI", "HBAR", "ICP", "INJ", "JUP", "LDO",
        "LINK", "LTC", "NEAR", "OP", "PEPE", "RENDER", "SEI", "SHIB",
        "SNX", "SOL", "STX", "SUI", "TAO", "TIA", "TRX", "UNI",
        "WIF", "WLD", "XLM", "XRP",
    )

    def __init__(self, horizon: int = 3) -> None:
        self.horizon = horizon
        self.strategy = None
        self.model_genes = None
        self.features: list[str] = []
        self._ready = False
        self._model = None
        self._model_ready = False
        self._model_error: str | None = None
        self._last_coverage: tuple[int, int] | None = None
        self._coin_categories: list[str] = []
        # 信号去抖: coin -> deque of recent raw BUY/HOLD flags (1=BUY)
        self._sig_buf: dict[str, list[int]] = {}
        self._debounce_k: int = 3   # 连续 N 根一致才确认
        self._last_sig_state: dict[str, bool] = {}
        # 模型概率 EMA 平滑 (减少 cross-sectional 排名抖动)
        self._smooth_probs: dict[str, float] = {}
        self._smooth_alpha: float = 0.5
        self._load_defaults()
        if self._ready:
            self._load_model()

    def _load_defaults(self) -> None:
        self._model_ready = False
        if _V18 is None:
            return
        try:
            loaded = self._load_best_chromosome()
            if loaded:
                self.strategy, self.model_genes = loaded
            else:
                seeds = _V18.elite_strategy_seeds(self.horizon)
                self.strategy = seeds[0]
                model_seeds = _V18.elite_model_seeds()
                self.model_genes = model_seeds[0]
            self.features = _V18.candidate_features(self.model_genes)
            self._ready = True
        except Exception:
            self._ready = False

    def _load_best_chromosome(self) -> tuple | None:
        if not BEST_JSON.exists():
            return None
        payload = json.loads(BEST_JSON.read_text(encoding="utf-8"))
        model_raw = payload.get("best_model_chromosome")
        strat_raw = payload.get("best_strategy_chromosome")
        if not model_raw or not strat_raw:
            return None
        model_genes = _V18.parse_model_genes(model_raw)
        signal_raw = strat_raw["signal"]
        risk_raw = strat_raw["risk"]
        exec_raw = strat_raw["execution"]
        strategy = _V18.StrategyChromosome(
            signal=_V18.SignalGenes(
                mode=str(signal_raw["mode"]),
                threshold=float(signal_raw["threshold"]),
                confirmation_indicator=str(signal_raw["confirmation_indicator"]),
                confirmation_window=int(signal_raw["confirmation_window"]),
                confirmation_direction=int(signal_raw["confirmation_direction"]),
            ),
            quantile=float(strat_raw["quantile"]),
            top_k=int(strat_raw["top_k"]),
            market_gate=float(strat_raw["market_gate"]),
            breadth_gate=float(strat_raw["breadth_gate"]),
            risk=_V18.RiskGenes(
                sizing_mode=str(risk_raw["sizing_mode"]),
                holding_bars=int(risk_raw["holding_bars"]),
                sl_tp_mode=str(risk_raw.get("sl_tp_mode") or "fixed"),
                stop_loss=float(risk_raw["stop_loss"]),
                take_profit=float(risk_raw["take_profit"]),
                trailing_stop=float(risk_raw["trailing_stop"]),
                cooldown_bars=int(risk_raw["cooldown_bars"]),
                max_position=float(risk_raw["max_position"]),
                cash_buffer=float(risk_raw["cash_buffer"]),
                volatility_cap=float(risk_raw["volatility_cap"]),
            ),
            execution=_V18.ExecutionGenes(
                rebalance_bars=int(exec_raw["rebalance_bars"]),
                turnover_cap=float(exec_raw["turnover_cap"]),
                fee_stress=float(exec_raw["fee_stress"]),
                slippage_stress=float(exec_raw["slippage_stress"]),
            ),
        )
        strategy = _V18.repair_strategy_chromosome(strategy, model_genes.horizon)
        return strategy, model_genes

    def _load_model(self) -> bool:
        """Load the persisted LightGBM booster produced by export_model.py.

        The model file is the exported artifact of the frozen best chromosome
        (models/short_term_ta_ga_model.txt), so startup is O(ms) and the
        deployed model is byte-identical to the verified one instead of being
        re-fitted on every gateway restart.
        """
        try:
            import lightgbm as lgb
        except Exception as exc:
            # 缺依赖不能拖垮整个网关导入; 记录错误并回退到动量排名
            self._model_ready = False
            self._model_error = f"lightgbm import failed: {type(exc).__name__}: {exc}"
            return False

        try:
            if not MODEL_FILE.exists():
                return False
            self._model = lgb.Booster(model_file=str(MODEL_FILE))
            model_features = list(self._model.feature_name())
            if model_features and model_features != self.features:
                self.features = model_features
            self._coin_categories = list(self._COIN_CATEGORIES)
            if len(self._coin_categories) != self.TRAIN_COINS:
                self._model_error = "coin categories count mismatch"
                return False
            self._model_ready = True
            self._model_error = None
            return True
        except Exception as exc:
            self._model_ready = False
            self._model_error = f"{type(exc).__name__}: {exc}"
            return False

    def _strategy_confirmation_features(self) -> list[str]:
        """Extra ta_* columns the strategy's confirmation filter + ATR stop/take need."""
        strat = self.strategy
        extra: list[str] = []
        # ATR 动态止盈止损需要 ta_atr_24
        if strat is not None and getattr(strat.risk, "sl_tp_mode", "fixed") == "atr":
            if "ta_atr_24" not in self.features:
                extra.append("ta_atr_24")
        if strat is None or not strat.signal.confirmation_window:
            return extra
        cw = int(strat.signal.confirmation_window)
        if strat.signal.mode == "trend_following":
            # 回测中 trend_following 用确认窗口做 ADX 过滤, 与 confirmation_indicator 无关
            feat = f"ta_adx_{max(6, cw)}"
            if feat not in self.features and feat not in extra:
                extra.append(feat)
            return extra
        if strat.signal.mode == "turtle_breakout":
            feat = f"ta_donchian_{cw}"
            if feat not in self.features and feat not in extra:
                extra.append(feat)
            return extra
        if strat.signal.mode in {"momentum_confirmation", "mean_reversion_confirmation"}:
            ind = strat.signal.confirmation_indicator
            if not ind:
                return extra
            feat = f"ta_{ind}_{cw}"
            if feat not in self.features and feat not in extra:
                extra.append(feat)
            return extra
        return extra

    def _compute_ta_features(self, coins: dict[str, pd.DataFrame]) -> None:
        """Compute only the ta_* columns required by self.features + strategy confirmation.

        Replicates add_ta_bank's exact formulas for the model's indicators but
        skips the other ~138 unused indicator columns, cutting replay feature
        cost. Unknown indicators fall back to the full add_ta_bank.
        """
        from ta.momentum import ROCIndicator, StochasticOscillator
        from ta.trend import ADXIndicator, IchimokuIndicator
        from ta.volume import ChaikinMoneyFlowIndicator, MFIIndicator

        known = {"adx", "cmf", "ichimoku", "mfi", "roc", "stoch", "atr"}
        needed: list[tuple[str, str, int]] = []
        wanted = list(self.features) + self._strategy_confirmation_features()
        for feat in wanted:
            if not feat.startswith("ta_"):
                continue
            parts = feat.split("_")
            if len(parts) < 3:
                continue
            if any(f[0] == feat for f in needed):
                continue
            try:
                window = int(parts[2])
            except ValueError:
                continue
            needed.append((feat, parts[1], window))
        if not needed:
            return
        if any(indicator not in known for _, indicator, _ in needed):
            # 出现未覆盖的指标: 一次性回退到完整 add_ta_bank, 避免在循环中途
            # return 导致部分币种缺列 (以及重复 concat 造成列名重复)
            _V18.add_ta_bank(coins)
            return

        for coin, frame in coins.items():
            close, high, low, volume = frame["close"], frame["high"], frame["low"], frame["volume"]
            new_cols: dict[str, pd.Series] = {}
            for feat, indicator, window in needed:
                if indicator == "adx":
                    new_cols[feat] = (ADXIndicator(high, low, close, window=window).adx() / 100.0).astype("float32")
                elif indicator == "cmf":
                    new_cols[feat] = ChaikinMoneyFlowIndicator(high, low, close, volume, window=window).chaikin_money_flow().astype("float32")
                elif indicator == "ichimoku":
                    iw = max(9, window)
                    ich = IchimokuIndicator(high, low, window1=iw, window2=max(26, iw), window3=max(52, iw * 2))
                    new_cols[feat] = ((close - ich.ichimoku_a()) / close).astype("float32")
                elif indicator == "mfi":
                    new_cols[feat] = (MFIIndicator(high, low, close, volume, window=window).money_flow_index() / 100.0).astype("float32")
                elif indicator == "roc":
                    new_cols[feat] = (ROCIndicator(close, window=window).roc() / 100.0).astype("float32")
                elif indicator == "stoch":
                    new_cols[feat] = (StochasticOscillator(high, low, close, window=window, smooth_window=max(2, window // 3)).stoch_signal() / 100.0).astype("float32")
                elif indicator == "atr":
                    from ta.volatility import AverageTrueRange
                    atr = AverageTrueRange(high, low, close, window=window).average_true_range()
                    new_cols[feat] = (atr / close).astype("float32")
            frame = pd.concat([frame, pd.DataFrame(new_cols)], axis=1)
            frame.replace([np.inf, -np.inf], np.nan, inplace=True)
            coins[coin] = frame

    @staticmethod
    def _writeback(frame: pd.DataFrame, feats: pd.DataFrame, col: str) -> None:
        """把 feats 的某列按尾部对齐写回 frame (长度不等时取尾部 min 段).

        frame 可能长于 FEATURE_HISTORY 而被裁剪, 直接按数组长度赋值会抛
        Length mismatch; 这里按尾部对齐, 保证任意长度差下都安全.
        """
        if col in frame.columns or col not in feats.columns:
            return
        values = feats[col].to_numpy(dtype="float32")
        n = min(len(frame), len(values))
        if n <= 0:
            return
        frame.loc[frame.index[-n:], col] = values[-n:]

    def _model_probs(self, frames: dict[str, pd.DataFrame]) -> dict[str, float]:
        """Score the latest bar of each coin with the live LightGBM model.

        Market features need the cross-coin close panel, so all coins are
        batched through add_base_features + add_ta_bank together. Coins with
        too little history for the model's TA windows are skipped (fallback to
        momentum ranking), never fed raw to indicator computation.
        """
        try:
            trimmed: dict[str, pd.DataFrame] = {}
            for coin, frame in frames.items():
                if frame is None or frame.empty or "close" not in frame.columns:
                    continue
                if len(frame) < 80:
                    continue
                trimmed[coin] = frame.iloc[-self.FEATURE_HISTORY:].reset_index(drop=True)
            if len(trimmed) < 3:
                return {}
            feats = {coin: f.copy() for coin, f in trimmed.items()}
            _V18.add_base_features(feats)
            _V18.add_micro_features(feats)
            _V18.add_cross_sectional_features(feats)
            self._compute_ta_features(feats)
            probs: dict[str, float] = {}
            for coin, f in feats.items():
                row = f[self.features].iloc[[-1]]
                if row.isna().any(axis=1).any():
                    continue
                row["coin"] = pd.Categorical(
                    [str(coin).replace("-USDT", "")], categories=self._coin_categories
                )
                p = self._model.predict(row, num_iteration=self._model.best_iteration)[0]
                probs[coin] = float(p)
            # EMA 平滑: 新概率与历史平滑值混合, 降低单 bar 波动对排名的冲击
            a = self._smooth_alpha
            for coin, p in probs.items():
                prev = self._smooth_probs.get(coin)
                self._smooth_probs[coin] = p if prev is None else a * p + (1 - a) * prev
            smoothed = {coin: self._smooth_probs[coin] for coin in probs}
            self._last_coverage = (len(smoothed), len(trimmed))
            self._model_error = None
            # 回写确认特征到原 frames, 供 signal_weights 的确认过滤使用
            confirm_cols = self._strategy_confirmation_features()
            for coin, f in feats.items():
                frame = frames.get(coin)
                if frame is None:
                    continue
                for col in confirm_cols:
                    self._writeback(frame, f, col)
                self._writeback(frame, f, "base_ret_24")
                # ATR 回写: 供 ATR 动态止盈止损使用 (缺失则退化为固定 0.02)
                self._writeback(frame, f, "ta_atr_24")
            return smoothed
        except Exception as exc:
            self._model_error = f"{type(exc).__name__}: {exc}"
            return {}

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def strategy_dict(self) -> dict | None:
        return asdict(self.strategy) if self.strategy is not None else None

    @property
    def model_dict(self) -> dict | None:
        return asdict(self.model_genes) if self.model_genes is not None else None

    def reset_runtime_state(self) -> None:
        """清空去抖/概率平滑等运行时状态 (feed 重启时调用, 避免跨重启残留旧信号)."""
        self._sig_buf = {}
        self._last_sig_state = {}
        self._smooth_probs = {}
        self._last_coverage = None

    def compute_features(self, frame: pd.DataFrame) -> pd.DataFrame:
        if not self._ready:
            raise RuntimeError("V18 source not loaded")
        local = frame.copy()
        for window in (1, 2, 3, 6, 12, 24, 48):
            local[f"base_ret_{window}"] = local["close"].pct_change(window)
        for window in (6, 12, 24, 48):
            local[f"base_vol_{window}"] = local["close"].pct_change().rolling(window).std()
            median_vol = local["volume"].rolling(window).median()
            local[f"base_volume_ratio_{window}"] = local["volume"] / median_vol
        return local

    def _momentum_scores(self, frames: dict[str, pd.DataFrame]) -> dict[str, float]:
        scores: dict[str, float] = {}
        for coin, frame in frames.items():
            if frame is None or frame.empty or "close" not in frame.columns:
                continue
            closes = frame["close"]
            if len(closes) >= MOMENTUM_WINDOW + 1:
                momentum = closes.pct_change(MOMENTUM_WINDOW).iloc[-1]
                scores[coin] = float(momentum) if not pd.isna(momentum) else 0.0
            else:
                scores[coin] = 0.0
        return scores

    def rank_signal(self, frames: dict[str, pd.DataFrame]) -> dict[str, float]:
        """Cross-sectional percentile rank over the latest bar per coin.

        Prefers live LightGBM model probabilities; falls back to 3-bar
        momentum ranking when the model is unavailable or too few coins have
        complete features.
        """
        if not self._ready or not self.strategy:
            return {}
        if self._model_ready:
            probs = self._model_probs(frames)
            if probs:
                ranks = pd.Series(probs).rank(pct=True)
                return {coin: float(rank) for coin, rank in ranks.items()}
        scores = self._momentum_scores(frames)
        if not scores:
            return {}
        ranks = pd.Series(scores).rank(pct=True)
        return {coin: float(rank) for coin, rank in ranks.items()}

    def _passes_confirmation(self, coin: str, frame: pd.DataFrame) -> bool:
        """Strategy confirmation filter, mirroring backtest_chromosome.

        trend_following: ADX >= 0.20 and base_ret_24 aligned with direction.
        confirmation modes: indicator-relative check.
        turtle_breakout: donchian >= 0.90.
        model_rank/momentum modes: no extra confirmation (rank-only).
        """
        strat = self.strategy
        if strat is None:
            return True
        mode = strat.signal.mode
        direction = int(strat.signal.confirmation_direction)
        cw = int(strat.signal.confirmation_window)
        if mode == "trend_following":
            adx_w = max(6, cw)
            adx_col = f"ta_adx_{adx_w}"
            if adx_col not in frame.columns or "base_ret_24" not in frame.columns:
                return False
            row = frame.iloc[-1]
            if pd.isna(row.get(adx_col)) or pd.isna(row.get("base_ret_24")):
                return False
            return float(row[adx_col]) >= 0.20 and float(row["base_ret_24"]) * direction > 0.0
        if mode == "turtle_breakout":
            col = f"ta_donchian_{cw}"
            if col not in frame.columns:
                return False
            val = frame.iloc[-1].get(col)
            return val is not None and not pd.isna(val) and float(val) >= 0.90
        if mode in {"momentum_confirmation", "mean_reversion_confirmation"}:
            col = f"ta_{strat.signal.confirmation_indicator}_{cw}"
            if col not in frame.columns:
                return False
            val = frame.iloc[-1].get(col)
            if val is None or pd.isna(val):
                return False
            v = float(val)
            if strat.signal.confirmation_indicator in {"rsi", "stoch", "donchian", "keltner"}:
                v = v - 0.5
            d = direction * (-1 if mode == "mean_reversion_confirmation" else 1)
            return v * d > 0.0
        return True

    def _debounce_buy(self, coin: str, raw_buy: bool) -> bool:
        """信号去抖: 币必须连续 _debounce_k 根一致才确认 BUY/HOLD 翻转.

        降低 cross-sectional 排名在相邻 bar 间的抖动, 避免信号频繁闪烁.
        """
        buf = self._sig_buf.setdefault(coin, [])
        buf.append(1 if raw_buy else 0)
        if len(buf) > self._debounce_k:
            buf.pop(0)
        if len(buf) < self._debounce_k:
            return self._last_sig_state.get(coin, False)
        if all(v == 1 for v in buf):
            self._last_sig_state[coin] = True
            return True
        if all(v == 0 for v in buf):
            self._last_sig_state[coin] = False
            return False
        return self._last_sig_state.get(coin, False)

    def signal_weights(self, frames: dict[str, pd.DataFrame]) -> list[dict]:
        """Rank coins cross-sectionally and produce weights.

        Uses the trained strategy chromosome's top_k/quantile/threshold to
        select coins (matching backtest_chromosome selection), falling back to
        3-bar momentum ranking when the model is unavailable.
        """
        if not self._ready or not self.strategy:
            return []
        model_probs: dict[str, float] = {}
        if self._model_ready:
            model_probs = self._model_probs(frames)
        if model_probs:
            s = pd.Series(model_probs)
            ranks = s.rank(pct=True)
            method = "model"
        else:
            scores = self._momentum_scores(frames)
            if not scores:
                return []
            s = pd.Series(scores)
            ranks = s.rank(pct=True)
            method = "momentum"

        strat = self.strategy
        # 强制最大 5 仓: top_k 上限 5, 单币仓位上限 20%
        top_k = min(5, max(2, int(strat.top_k)))
        quantile = float(strat.quantile)

        cutoff = float(s.quantile(quantile)) if len(s) > 1 else float(s.min())
        eligible = s[s >= cutoff]
        top = eligible.nlargest(top_k).index.tolist()
        if not top:
            top = s.nlargest(top_k).index.tolist()

        # 权重: 与回测 sizing 一致 — score_weighted 按模型概率/动量分数加权,
        # 其余模式等权. 目标仓位 = 1 - cash_buffer, 单币上限 max_position.
        threshold = float(strat.signal.threshold)
        sizing = strat.risk.sizing_mode
        cash_buffer = float(strat.risk.cash_buffer)
        max_position = 0.20  # 最大5仓, 每仓上限20%
        target_total = min(1.0 - cash_buffer, max_position * len(top))
        if sizing == "score_weighted":
            scores_arr = s[top].clip(lower=0.0).to_numpy(dtype="float64")
            total_score = max(float(scores_arr.sum()), 1e-12)
            raw_weights = scores_arr / total_score
            weights_arr = raw_weights * target_total
        elif sizing == "inverse_vol":
            weights_arr = np.full(len(top), target_total / max(len(top), 1))
        else:
            weights_arr = np.full(len(top), target_total / max(len(top), 1))
        weight_map = dict(zip(top, weights_arr, strict=False))
        out: list[dict] = []
        for coin in top:
            latest = frames[coin].iloc[-1]
            if method == "model":
                # 模型模式: 按策略模式的确认过滤决定 BUY/HOLD (与回测 selection 一致).
                # trend_following/turtle: 只用确认过滤 (ADX/趋势 or donchian), 不看绝对概率阈值;
                # model_rank/confirmation 模式: 概率 >= 阈值 才算 BUY.
                mode = strat.signal.mode
                if mode in {"model_rank", "momentum_confirmation", "mean_reversion_confirmation"}:
                    raw_buy = float(model_probs[coin]) >= threshold
                else:
                    raw_buy = self._passes_confirmation(coin, frames[coin])
            else:
                # 动量回退: 排名前 60% 视为 BUY
                raw_buy = ranks[coin] >= 0.6
            buy = self._debounce_buy(coin, raw_buy)
            # 止盈止损: ATR 模式按该币当前 ATR 动态计算, fixed 用固定百分比
            sl_pct = float(strat.risk.stop_loss)
            tp_pct = float(strat.risk.take_profit)
            if strat.risk.sl_tp_mode == "atr":
                frame = frames[coin]
                atr = None
                if "ta_atr_24" in frame.columns:
                    atr = frame["ta_atr_24"].iloc[-1]
                atr_val = float(atr) if atr is not None and pd.notna(atr) and float(atr) > 0 else 0.02
                sl_pct = strat.risk.stop_loss * atr_val
                tp_pct = strat.risk.take_profit * atr_val
            item: dict = {
                "coin": coin,
                "rank": round(ranks[coin], 3),
                "weight": round(weight_map[coin], 4),
                "close": float(latest["close"]),
                "timestamp": str(latest["timestamp"]),
                "signal": "BUY" if buy else "HOLD",
                "signal_method": method,
                "score": round(float(s[coin]), 6),
                "threshold": round(threshold, 6),
                "sizing": sizing,
                "cash_buffer": round(cash_buffer, 4),
                "max_position": round(max_position, 4),
                "sl_tp_mode": strat.risk.sl_tp_mode,
                "take_profit": round(tp_pct, 4),
                "stop_loss": round(sl_pct, 4),
            }
            if method == "model":
                item["model_prob"] = round(model_probs[coin], 6)
            out.append(item)
        return out

    def portfolio_summary(self, frames: dict[str, pd.DataFrame]) -> dict:
        closes: dict[str, float] = {}
        for coin, frame in frames.items():
            if not frame.empty:
                closes[coin] = float(frame.iloc[-1]["close"])
        if not closes:
            return {}
        series = pd.Series(closes)
        return {
            "coins": len(closes),
            "total_market_cap": float(series.sum()),
            "mean": float(series.mean()),
            "median": float(series.median()),
            "top_coin": series.idxmax(),
            "top_price": float(series.max()),
            "lowest_coin": series.idxmin(),
            "lowest_price": float(series.min()),
        }
