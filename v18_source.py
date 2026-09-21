# NOTE: short_term_ta_ga_local.py and short-term-ta-ga-v6-quant-chromosome.py are
# intended duplicates. The ONLY differences are INPUT_DIR/OUTPUT_DIR and this sync
# notice. Keep them in sync — any other change MUST be applied to BOTH files.
from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Final, Protocol, TypeAlias

import numpy as np
try:
    import jax  # noqa: JAX_OK
    from jax_backtest import jax_strategy_fitness_batch
    _HAS_JAX = True
except ImportError:
    _HAS_JAX = False
import pandas as pd  # noqa: PANDAS_OK
from multiprocessing import Pool, cpu_count

for _p in ("/kaggle/src", "/kaggle/working", str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SEED: Final = int(os.environ.get("QUANT_SEED", "37"))
INPUT_DIR: Final = Path("/home/thirty/quant-local/data/okx_4h_4y_indicators")
OUTPUT_DIR: Final = Path("/home/thirty/quant-local/output")
FEE: Final = 0.003
SLIPPAGE: Final = 0.0005
ROUND_TRIP_COST: Final = 2.0 * (FEE + SLIPPAGE)
SHORT_HORIZONS: Final = (1, 2, 3)
HORIZONS: Final = SHORT_HORIZONS
WINDOWS: Final = (3, 6, 9, 12, 18, 24, 36, 48)
INDICATORS: Final = (
    "rsi", "macd", "bollinger", "atr", "stoch", "adx", "mfi", "obv",
    "donchian", "cci", "roc", "williams", "keltner", "ichimoku",
    "aroon", "cmf", "force", "vwap",
)
MODEL_POPULATION: Final = 12
MODEL_GENERATIONS: Final = 8
_ON_KAGGLE: Final = Path("/home/thirty/quant-local/data/okx_4h_4y_indicators").exists()
CHAMPION_JSON: Final = Path("/home/thirty/quant-trader/models/best_chromosome.json")
STRATEGY_POPULATION: Final = int(os.environ.get("QUANT_STRATEGY_POPULATION", "50" if _ON_KAGGLE else "80"))
STRATEGY_GENERATIONS: Final = int(os.environ.get("QUANT_STRATEGY_GENERATIONS", "90" if _ON_KAGGLE else "180"))
MAX_HOLDING_BARS: Final = 6
ISLAND_COUNT: Final = int(os.environ.get("QUANT_ISLAND_COUNT", "4" if _ON_KAGGLE else "4"))
ISLAND_POPULATION: Final = int(os.environ.get("QUANT_ISLAND_POPULATION", "8" if _ON_KAGGLE else "20"))
ISLAND_GENERATIONS: Final = int(os.environ.get("QUANT_ISLAND_GENERATIONS", "5" if _ON_KAGGLE else "7"))
MIGRATION_INTERVAL: Final = 4
MAX_TRAIN_COINS: Final = int(os.environ.get("QUANT_MAX_COINS", "0"))
NUM_THREADS: Final = int(os.environ.get("QUANT_NUM_THREADS", "8" if _ON_KAGGLE else "4"))
AUC_TARGET: Final = 0.70
MODEL_FITNESS: Final = "return"  # "return" | "auc" — model GA fitness objective
# 目标模式: "binary"=未来收益≥70分位分类 | "regression"=直接回归未来收益 (改善 rank_ic)
TARGET_MODE: Final = os.environ.get("QUANT_TARGET_MODE", "binary")
ENSEMBLE: Final = False
ENSEMBLE_K: Final = 5
HIGH_FREQUENCY_INTERVALS: Final = (1, 2, 3)
MAX_MODEL_CANDIDATES: Final = ISLAND_COUNT * ISLAND_POPULATION * ISLAND_GENERATIONS
FINAL_FRACTION: Final = 0.15
# 固定截止日期 (如 "2026-08-21"): 设置后模型训练到该日期, 之后为真实 OOS
FINAL_CUTOFF: Final = os.environ.get("QUANT_FINAL_CUTOFF")
FOLD_RATIOS: Final = (
    ((0.40, 0.60), (0.60, 0.78), (0.78, 0.84))
    if _ON_KAGGLE
    else ((0.40, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 0.78), (0.78, 0.84))
)
PRECOMPUTED_FEATURES: Final = (
    "return_1", "return_4", "return_8", "return_16",
    "log_return_1", "log_return_4", "log_return_8", "log_return_16",
    "volatility_8", "volatility_20",
    "close_position", "bb_position", "hl_range_pct", "gap_open_pct",
    "volume_ratio_20", "volume_ratio_50",
    "ema_7_25_ratio", "ema_25_50_ratio", "ema_50_200_ratio",
)
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class ModelGenes:
    horizon: int
    indicators: tuple[str, ...]
    windows: tuple[int, ...]
    learning_rate: float
    max_depth: int
    num_leaves: int
    min_child_samples: int
    feature_fraction: float
    bagging_fraction: float
    reg_alpha: float
    reg_lambda: float
    n_estimators: int


RECOVERY_GENES: Final = ModelGenes(
    horizon=12,
    indicators=("atr", "macd", "obv"),
    windows=(18, 48, 48),
    learning_rate=0.0243,
    max_depth=6,
    num_leaves=18,
    min_child_samples=132,
    feature_fraction=0.769,
    bagging_fraction=0.908,
    reg_alpha=1.12,
    reg_lambda=1.73,
    n_estimators=256,
)


@dataclass(frozen=True, slots=True)
class StrategyGenes:
    quantile: float
    top_k: int
    rebalance_every: int
    market_gate: float


@dataclass(frozen=True, slots=True)
class SignalGenes:
    mode: str
    threshold: float
    confirmation_indicator: str
    confirmation_window: int
    confirmation_direction: int


@dataclass(frozen=True, slots=True)
class RiskGenes:
    sizing_mode: str
    holding_bars: int
    sl_tp_mode: str  # "fixed"=固定百分比止损止盈 | "atr"=ATR 动态倍数
    stop_loss: float  # fixed: 百分比(0.10=10%); atr: ATR 倍数(2.5=2.5×ATR)
    take_profit: float  # fixed: 百分比; atr: ATR 倍数
    trailing_stop: float
    cooldown_bars: int
    max_position: float
    cash_buffer: float
    volatility_cap: float


@dataclass(frozen=True, slots=True)
class ExecutionGenes:
    rebalance_bars: int
    turnover_cap: float
    fee_stress: float
    slippage_stress: float


@dataclass(frozen=True, slots=True)
class StrategyChromosome:
    signal: SignalGenes
    quantile: float
    top_k: int
    market_gate: float
    breadth_gate: float
    risk: RiskGenes
    execution: ExecutionGenes


def repair_strategy_chromosome(chromosome: StrategyChromosome, model_horizon: int) -> StrategyChromosome:
    holding_bars = max(model_horizon, chromosome.risk.holding_bars)
    rebalance_bars = max(holding_bars, chromosome.execution.rebalance_bars)
    sl_tp_mode = chromosome.risk.sl_tp_mode if chromosome.risk.sl_tp_mode in ("fixed", "atr") else "fixed"
    if sl_tp_mode == "atr":
        # ATR 倍数: 止损 0.5-3×ATR, 止盈 1-5×ATR, 至少比止损高 0.5×ATR (防止宽到不触发)
        stop_loss = min(max(chromosome.risk.stop_loss, 0.5), 3.0)
        take_profit = max(chromosome.risk.take_profit, stop_loss + 0.5)
        take_profit = min(take_profit, 5.0)
        trailing_stop = min(max(chromosome.risk.trailing_stop, 0.0), take_profit)
    else:
        # fixed 限制在真实交易范围 (0.5%-6% 止损, 1%-12% 止盈), 防止宽止损作弊
        stop_loss = min(max(chromosome.risk.stop_loss, 0.005), 0.06)
        take_profit = max(chromosome.risk.take_profit, stop_loss + 0.005)
        take_profit = min(take_profit, 0.12)
        trailing_stop = min(max(chromosome.risk.trailing_stop, 0.0), take_profit)
    top_k = min(max(2, chromosome.top_k), 5)
    cash_buffer = min(max(chromosome.risk.cash_buffer, 0.0), 0.90)
    max_position = min(max(chromosome.risk.max_position, 0.01), 1.0 - cash_buffer)
    return StrategyChromosome(
        signal=chromosome.signal,
        quantile=min(max(chromosome.quantile, 0.50), 0.98),
        top_k=top_k,
        market_gate=chromosome.market_gate,
        breadth_gate=min(max(chromosome.breadth_gate, 0.0), 1.0),
        risk=RiskGenes(
            sizing_mode=chromosome.risk.sizing_mode,
            holding_bars=holding_bars,
            sl_tp_mode=sl_tp_mode,
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_stop=trailing_stop,
            cooldown_bars=max(0, chromosome.risk.cooldown_bars),
            max_position=max_position,
            cash_buffer=cash_buffer,
            volatility_cap=max(0.001, chromosome.risk.volatility_cap),
        ),
        execution=ExecutionGenes(
            rebalance_bars=rebalance_bars,
            turnover_cap=min(max(chromosome.execution.turnover_cap, 0.01), 2.0),
            fee_stress=max(FEE, chromosome.execution.fee_stress),
            slippage_stress=max(SLIPPAGE, chromosome.execution.slippage_stress),
        ),
    )


def default_strategy_chromosome(model_horizon: int) -> StrategyChromosome:
    return random_strategy_chromosome(random.Random(SEED), model_horizon)


def random_strategy_chromosome(rng: random.Random, model_horizon: int) -> StrategyChromosome:
    raw = StrategyChromosome(
        signal=SignalGenes(
            mode=rng.choice((
                "model_rank", "momentum_confirmation", "mean_reversion_confirmation",
                "turtle_breakout", "trend_following",
            )),
            threshold=round(rng.uniform(0.42, 0.60), 3),
            confirmation_indicator=rng.choice(("rsi", "macd", "adx", "stoch")),
            confirmation_window=rng.choice(WINDOWS),
            confirmation_direction=rng.choice((-1, 1)),
        ),
            quantile=rng.choice((0.50, 0.60, 0.70, 0.75, 0.80)),
        top_k=rng.choice((1, 2, 3, 5)),
            market_gate=rng.choice((-0.02, -0.01, 0.0, 0.005)),
            breadth_gate=rng.choice((0.20, 0.30, 0.40, 0.50)),
        risk=RiskGenes(
            sizing_mode=rng.choice(("equal", "inverse_vol", "score_weighted")),
            holding_bars=rng.choice((1, 2, 3, 6, 12)),
            sl_tp_mode=rng.choice(("fixed", "atr")),
            # fixed: 0.5%-4% 止损 / 1%-6% 止盈; atr: 0.5-3× / 1-5×
            stop_loss=round(rng.uniform(0.5, 3.0), 3) if rng.random() < 0.5 else round(rng.uniform(0.005, 0.04), 3),
            take_profit=round(rng.uniform(1.0, 5.0), 3) if rng.random() < 0.5 else round(rng.uniform(0.01, 0.06), 3),
            trailing_stop=round(rng.uniform(0.004, 0.030), 3),
            cooldown_bars=rng.choice((0, 1, 2, 3, 6)),
            max_position=round(rng.uniform(0.10, 0.50), 3),
            cash_buffer=round(rng.uniform(0.0, 0.30), 3),
            volatility_cap=round(rng.uniform(0.02, 0.20), 3),
        ),
        execution=ExecutionGenes(
            rebalance_bars=rng.choice((3, 3, 6, 6, 12)),
            turnover_cap=round(rng.uniform(0.05, 0.40), 3),
            fee_stress=FEE,
            slippage_stress=SLIPPAGE,
        ),
    )
    return repair_strategy_chromosome(raw, model_horizon)


def elite_strategy_seeds(model_horizon: int) -> list[StrategyChromosome]:
    base = random_strategy_chromosome(random.Random(SEED), model_horizon)
    champion_v15 = StrategyChromosome(
        signal=SignalGenes("mean_reversion_confirmation", 0.70, "stoch", 48, -1),
        quantile=0.75,
        top_k=2,
        market_gate=-0.01,
        breadth_gate=0.5,
        risk=RiskGenes("score_weighted", 3, "fixed", 0.019, 0.06, 0.002, 2, 0.391, 0.075, 0.072),
        execution=ExecutionGenes(6, 0.102, FEE, SLIPPAGE),
    )
    seeds = [
        replace(base, signal=SignalGenes("turtle_breakout", 0.55, "adx", 24, 1), quantile=0.60, top_k=5),
        replace(base, signal=SignalGenes("trend_following", 0.55, "adx", 24, 1), quantile=0.60, top_k=5),
        replace(base, signal=SignalGenes("mean_reversion_confirmation", 0.52, "rsi", 12, 1), quantile=0.60, top_k=3),
        replace(base, signal=SignalGenes("model_rank", 0.50, "macd", 12, 1), quantile=0.50, top_k=5),
        champion_v15,
    ]
    champion = load_champion()
    if champion is not None and champion[1] is not None:
        seeds.insert(0, champion[1])
    return [repair_strategy_chromosome(seed, model_horizon) for seed in seeds]


@dataclass(frozen=True, slots=True)
class Fold:
    fold_id: int
    train_end: pd.Timestamp
    valid_start: pd.Timestamp
    valid_end: pd.Timestamp


class DatasetContractError(RuntimeError):
    pass


class ModelBudgetError(RuntimeError):
    pass


class Booster(Protocol):
    best_iteration: int

    def predict(self, data: pd.DataFrame, num_iteration: int | None = None) -> np.ndarray: ...

    def save_model(self, filename: str, num_iteration: int | None = None) -> Booster: ...

    def num_trees(self) -> int: ...


def ensure_ta() -> None:
    try:
        import ta  # noqa: F401
    except ModuleNotFoundError:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "ta==0.11.0"],
            check=True,
        )


def ensure_deps() -> None:
    try:
        import lightgbm  # noqa: F401
    except ModuleNotFoundError:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "lightgbm"],
            check=True,
        )


def has_gpu() -> bool:
    if os.environ.get("QUANT_LOCAL_FORCE_CPU") == "1":
        return False
    for probe in ("nvidia-smi", "rocm-smi"):
        try:
            result = subprocess.run([probe], check=False, capture_output=True)
        except OSError:
            continue
        if result.returncode == 0:
            return True
    return False


def stable_digest(genes: ModelGenes) -> str:
    payload = json.dumps(asdict(genes), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def parse_model_genes(raw: dict[str, JsonValue]) -> ModelGenes:
    indicators = tuple(str(value) for value in raw["indicators"])
    windows = tuple(int(value) for value in raw["windows"])
    return ModelGenes(
        horizon=int(raw["horizon"]),
        indicators=indicators,
        windows=windows,
        learning_rate=float(raw["learning_rate"]),
        max_depth=int(raw["max_depth"]),
        num_leaves=int(raw["num_leaves"]),
        min_child_samples=int(raw["min_child_samples"]),
        feature_fraction=float(raw["feature_fraction"]),
        bagging_fraction=float(raw["bagging_fraction"]),
        reg_alpha=float(raw["reg_alpha"]),
        reg_lambda=float(raw["reg_lambda"]),
        n_estimators=int(raw["n_estimators"]),
    )


def checkpoint_candidate() -> ModelGenes | None:
    checkpoints = sorted(INPUT_DIR.rglob("ga_checkpoint.json"))
    if not checkpoints:
        return None
    payload = json.loads(checkpoints[-1].read_text(encoding="utf-8"))
    return parse_model_genes(payload["best_genes"])


def parse_strategy_chromosome(raw: dict[str, JsonValue], model_horizon: int) -> StrategyChromosome:
    sig = raw["signal"]
    risk = raw["risk"]
    exec_ = raw["execution"]
    return repair_strategy_chromosome(
        StrategyChromosome(
            signal=SignalGenes(
                mode=str(sig["mode"]),
                threshold=float(sig["threshold"]),
                confirmation_indicator=str(sig["confirmation_indicator"]),
                confirmation_window=int(sig["confirmation_window"]),
                confirmation_direction=int(sig["confirmation_direction"]),
            ),
            quantile=float(raw["quantile"]),
            top_k=int(raw["top_k"]),
            market_gate=float(raw["market_gate"]),
            breadth_gate=float(raw["breadth_gate"]),
            risk=RiskGenes(
                sizing_mode=str(risk["sizing_mode"]),
                holding_bars=int(risk["holding_bars"]),
                sl_tp_mode=str(risk.get("sl_tp_mode") or "fixed"),
                stop_loss=float(risk["stop_loss"]),
                take_profit=float(risk["take_profit"]),
                trailing_stop=float(risk["trailing_stop"]),
                cooldown_bars=int(risk["cooldown_bars"]),
                max_position=float(risk["max_position"]),
                cash_buffer=float(risk["cash_buffer"]),
                volatility_cap=float(risk["volatility_cap"]),
            ),
            execution=ExecutionGenes(
                rebalance_bars=int(exec_["rebalance_bars"]),
                turnover_cap=float(exec_["turnover_cap"]),
                fee_stress=float(exec_["fee_stress"]),
                slippage_stress=float(exec_["slippage_stress"]),
            ),
        ),
        model_horizon,
    )


def load_champion() -> tuple[ModelGenes, StrategyChromosome] | None:
    if not CHAMPION_JSON.exists():
        return None
    try:
        payload = json.loads(CHAMPION_JSON.read_text(encoding="utf-8"))
        model_genes = parse_model_genes(payload["best_model_chromosome"])
        strategy = parse_strategy_chromosome(payload["best_strategy_chromosome"], model_genes.horizon)
        return model_genes, strategy
    except Exception:
        return None


def load_ohlcv() -> dict[str, pd.DataFrame]:
    paths = sorted(INPUT_DIR.rglob("*.parquet"))
    if not paths:
        raise FileNotFoundError(f"No parquet files found under {INPUT_DIR}")

    coins: dict[str, pd.DataFrame] = {}
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    for path in paths:
        coin = path.stem.replace("_USDT_USDT_indicators", "").replace("_indicators", "").replace("_USDT_SWAP", "")
        raw = pd.read_parquet(path, columns=required)
        frame = raw.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
        if not pd.api.types.is_datetime64_any_dtype(frame["timestamp"]):
            ts = frame["timestamp"]
            unit = "ms" if (ts.abs() > 1e12).any() else "s"
            frame["timestamp"] = pd.to_datetime(ts, utc=True, unit=unit)
        else:
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        if len(frame) >= 1000 and (frame[["open", "high", "low", "close"]] > 0).all().all():
            coins[coin] = frame.astype(
                {"open": "float32", "high": "float32", "low": "float32", "close": "float32", "volume": "float32"}
            )
    if len(coins) < 10:
        raise DatasetContractError(f"Only {len(coins)} valid coins were loaded")
    return coins


def add_base_features(coins: dict[str, pd.DataFrame]) -> None:
    close_panel = pd.DataFrame({coin: frame.set_index("timestamp")["close"] for coin, frame in coins.items()})
    market_return = close_panel.pct_change().mean(axis=1)
    market_momentum = close_panel.pct_change(6).mean(axis=1)
    market_breadth = close_panel.pct_change(3).gt(0).mean(axis=1)
    for coin, frame in coins.items():
        close = frame["close"]
        volume = frame["volume"]
        for window in (1, 2, 3, 6, 12, 24, 48):
            frame[f"base_ret_{window}"] = close.pct_change(window).astype("float32")
        for window in (6, 12, 24, 48):
            frame[f"base_vol_{window}"] = close.pct_change().rolling(window).std().astype("float32")
            frame[f"base_volume_ratio_{window}"] = (volume / volume.rolling(window).median()).astype("float32")
        timestamps = frame["timestamp"]
        frame["market_return"] = market_return.reindex(timestamps).to_numpy(dtype="float32")
        frame["market_momentum"] = market_momentum.reindex(timestamps).to_numpy(dtype="float32")
        frame["market_breadth"] = market_breadth.reindex(timestamps).to_numpy(dtype="float32")
        frame["coin"] = coin


MICRO_WINDOWS: Final = (6, 12, 24)


def add_micro_features(coins: dict[str, pd.DataFrame]) -> None:
    """市场微结构因子: 蜡烛形态(实体/影线/振幅) + 量价关系.

    这些维度是技术指标(RSI/MACD 等)未覆盖的: 实体占比捕捉单根趋势强度,
    影线占比捕捉买卖压力, 量变化捕捉放量/缩量。从 OHLCV 直接计算, 无需外部数据。
    """
    for frame in coins.values():
        o = frame["open"]
        h = frame["high"]
        l = frame["low"]
        c = frame["close"]
        v = frame["volume"]
        rng = (h - l).replace(0.0, np.nan)

        frame["micro_body"] = ((c - o).abs() / rng).astype("float32")
        frame["micro_upper_wick"] = ((h - np.maximum(o, c)) / rng).astype("float32")
        frame["micro_lower_wick"] = ((np.minimum(o, c) - l) / rng).astype("float32")
        frame["micro_intraday"] = ((c / o) - 1.0).astype("float32")
        frame["micro_range"] = (rng / c).astype("float32")
        frame["micro_gap"] = ((o / c.shift(1)) - 1.0).astype("float32")
        frame["micro_vol_chg"] = ((v / v.shift(1)) - 1.0).astype("float32")

        for w in MICRO_WINDOWS:
            frame[f"micro_body_{w}"] = frame["micro_body"].rolling(w).mean().astype("float32")
            frame[f"micro_intraday_{w}"] = frame["micro_intraday"].rolling(w).mean().astype("float32")
            frame[f"micro_vol_chg_{w}"] = (v / v.rolling(w).mean()).astype("float32")


def add_cross_sectional_features(coins: dict[str, pd.DataFrame]) -> None:
    """横截面相对特征: 超额收益 + 横截面排名 + 相对成交量.

    对抗 coin 特征主导: 把模型从"哪个币"转向"哪个币在跑赢".
    """
    close_panel = pd.DataFrame({coin: frame.set_index("timestamp")["close"] for coin, frame in coins.items()})
    volume_panel = pd.DataFrame({coin: frame.set_index("timestamp")["volume"] for coin, frame in coins.items()})
    for window in (1, 2, 3, 6, 12, 24, 48):
        ret_panel = close_panel.pct_change(window)
        excess = ret_panel.sub(ret_panel.mean(axis=1), axis=0)
        rank = ret_panel.rank(axis=1, pct=True)
        for coin, frame in coins.items():
            ts = frame["timestamp"]
            frame[f"excess_ret_{window}"] = excess[coin].reindex(ts).to_numpy(dtype="float32")
            frame[f"cs_rank_{window}"] = rank[coin].reindex(ts).to_numpy(dtype="float32")
    rel_vol = volume_panel.div(volume_panel.median(axis=1), axis=0)
    for coin, frame in coins.items():
        ts = frame["timestamp"]
        frame["rel_volume"] = rel_vol[coin].reindex(ts).to_numpy(dtype="float32")


def add_ta_bank(coins: dict[str, pd.DataFrame]) -> None:
    from ta.momentum import RSIIndicator, StochasticOscillator
    from ta.momentum import ROCIndicator, WilliamsRIndicator
    from ta.trend import ADXIndicator, CCIIndicator, IchimokuIndicator, MACD
    from ta.volatility import AverageTrueRange, BollingerBands, KeltnerChannel
    from ta.volume import ChaikinMoneyFlowIndicator, ForceIndexIndicator, MFIIndicator, OnBalanceVolumeIndicator, VolumeWeightedAveragePrice
    from ta.volatility import DonchianChannel

    for coin, frame in coins.items():
        close, high, low, volume = frame["close"], frame["high"], frame["low"], frame["volume"]
        new_cols: dict[str, pd.Series] = {}
        obv = OnBalanceVolumeIndicator(close=close, volume=volume, fillna=False).on_balance_volume()
        new_cols["ta_obv_1"] = obv.pct_change().replace([np.inf, -np.inf], np.nan).astype("float32")
        for window in WINDOWS:
            new_cols[f"ta_rsi_{window}"] = (RSIIndicator(close, window=window).rsi() / 100.0).astype("float32")
            fast = max(2, window // 2)
            slow = max(fast + 1, window)
            signal = max(2, window // 3)
            macd = MACD(close, window_slow=slow, window_fast=fast, window_sign=signal)
            new_cols[f"ta_macd_{window}"] = (macd.macd_diff() / close).astype("float32")
            bollinger = BollingerBands(close, window=window, window_dev=2)
            new_cols[f"ta_bollinger_{window}"] = bollinger.bollinger_pband().astype("float32")
            atr = AverageTrueRange(high, low, close, window=window).average_true_range()
            new_cols[f"ta_atr_{window}"] = (atr / close).astype("float32")
            stoch = StochasticOscillator(high, low, close, window=window, smooth_window=max(2, window // 3))
            new_cols[f"ta_stoch_{window}"] = (stoch.stoch_signal() / 100.0).astype("float32")
            if window >= 6:
                new_cols[f"ta_adx_{window}"] = (ADXIndicator(high, low, close, window=window).adx() / 100.0).astype("float32")
                new_cols[f"ta_mfi_{window}"] = (MFIIndicator(high, low, close, volume, window=window).money_flow_index() / 100.0).astype("float32")
            donchian = DonchianChannel(high, low, close, window=window)
            upper = donchian.donchian_channel_hband().shift(1)
            lower = donchian.donchian_channel_lband().shift(1)
            width = (upper - lower).replace(0.0, np.nan)
            new_cols[f"ta_donchian_{window}"] = ((close - lower) / width).astype("float32")
            new_cols[f"ta_cci_{window}"] = (CCIIndicator(high, low, close, window=window).cci() / 200.0).astype("float32")
            new_cols[f"ta_roc_{window}"] = (ROCIndicator(close, window=window).roc() / 100.0).astype("float32")
            new_cols[f"ta_williams_{window}"] = (WilliamsRIndicator(high, low, close, lbp=window).williams_r() / 100.0).astype("float32")
            keltner = KeltnerChannel(high, low, close, window=window)
            new_cols[f"ta_keltner_{window}"] = keltner.keltner_channel_pband().astype("float32")
            ichimoku_window = max(9, window)
            ichimoku = IchimokuIndicator(high, low, window1=ichimoku_window, window2=max(26, ichimoku_window), window3=max(52, ichimoku_window * 2))
            new_cols[f"ta_ichimoku_{window}"] = ((close - ichimoku.ichimoku_a()) / close).astype("float32")
            aroon_up = high.rolling(window).apply(lambda values: float(np.argmax(values)) / max(1, window - 1), raw=True)
            aroon_down = low.rolling(window).apply(lambda values: float(np.argmin(values)) / max(1, window - 1), raw=True)
            new_cols[f"ta_aroon_{window}"] = (aroon_up - aroon_down).astype("float32")
            new_cols[f"ta_cmf_{window}"] = ChaikinMoneyFlowIndicator(high, low, close, volume, window=window).chaikin_money_flow().astype("float32")
            force = ForceIndexIndicator(close, volume, window=window).force_index()
            new_cols[f"ta_force_{window}"] = (force / volume.rolling(window).median()).astype("float32")
            vwap = VolumeWeightedAveragePrice(high, low, close, volume, window=window).volume_weighted_average_price()
            new_cols[f"ta_vwap_{window}"] = ((close - vwap) / close).astype("float32")
        frame = pd.concat([frame, pd.DataFrame(new_cols)], axis=1)
        frame.replace([np.inf, -np.inf], np.nan, inplace=True)
        coins[coin] = frame


def random_model_genes(rng: random.Random) -> ModelGenes:
    indicator_count = rng.randint(3, 6)
    selected = tuple(sorted(rng.sample(INDICATORS, indicator_count)))
    windows = tuple(rng.choice(WINDOWS) for _ in selected)
    max_depth = rng.randint(3, 8)
    leaves = rng.randint(8, min(128, 2**max_depth))
    return ModelGenes(
        horizon=rng.choice(HORIZONS),
        indicators=selected,
        windows=windows,
        learning_rate=round(rng.uniform(0.01, 0.05), 4),
        max_depth=max_depth,
        num_leaves=max(2, min(leaves, 2**max_depth)),
        min_child_samples=rng.randint(120, 400),
        feature_fraction=round(rng.uniform(0.5, 0.85), 3),
        bagging_fraction=round(rng.uniform(0.55, 0.9), 3),
        reg_alpha=round(rng.uniform(0.2, 1.5), 3),
        reg_lambda=round(rng.uniform(0.5, 3.0), 3),
        n_estimators=rng.choice((96, 128, 160, 192, 256)),
    )


def random_island_genes(rng: random.Random, island: int) -> ModelGenes:
    base = random_model_genes(rng)
    if island == 0:
        indicators = tuple(sorted(rng.sample(("rsi", "macd", "bollinger", "stoch", "adx", "donchian", "roc"), 4)))
        learning_rate = round(rng.uniform(0.005, 0.035), 4)
    elif island == 1:
        indicators = tuple(sorted(rng.sample(("atr", "mfi", "obv", "bollinger", "rsi", "cci", "keltner"), 4)))
        learning_rate = round(rng.uniform(0.01, 0.06), 4)
    elif island == 2:
        indicators = tuple(sorted(rng.sample(("donchian", "roc", "williams", "aroon", "ichimoku", "adx", "macd"), 4)))
        learning_rate = round(rng.uniform(0.004, 0.03), 4)
    else:
        indicators = tuple(sorted(rng.sample(("cmf", "force", "vwap", "obv", "mfi", "keltner", "bollinger"), 4)))
        learning_rate = round(rng.uniform(0.003, 0.02), 4)
    return ModelGenes(
        horizon=rng.choice(SHORT_HORIZONS),
        indicators=indicators,
        windows=tuple(rng.choice(WINDOWS) for _ in indicators),
        learning_rate=learning_rate,
        max_depth=base.max_depth,
        num_leaves=base.num_leaves,
        min_child_samples=base.min_child_samples,
        feature_fraction=base.feature_fraction,
        bagging_fraction=base.bagging_fraction,
        reg_alpha=base.reg_alpha,
        reg_lambda=base.reg_lambda,
        n_estimators=base.n_estimators,
    )


def elite_model_seeds() -> list[ModelGenes]:
    champion_v15 = ModelGenes(
        horizon=3,
        indicators=("adx", "bollinger", "donchian", "macd"),
        windows=(48, 3, 6, 3),
        learning_rate=0.0377,
        max_depth=7,
        num_leaves=55,
        min_child_samples=202,
        feature_fraction=0.824,
        bagging_fraction=0.825,
        reg_alpha=0.623,
        reg_lambda=1.986,
        n_estimators=192,
    )
    return [
        replace(RECOVERY_GENES, horizon=1, indicators=("rsi", "macd", "adx", "roc"), windows=(12, 12, 24, 12)),
        replace(RECOVERY_GENES, horizon=2, indicators=("bollinger", "atr", "mfi", "cmf"), windows=(18, 18, 18, 18)),
        replace(RECOVERY_GENES, horizon=3, indicators=("donchian", "cci", "force", "vwap"), windows=(24, 24, 12, 12)),
        champion_v15,
    ]


def elite_model_seeds_with_champion() -> list[ModelGenes]:
    seeds = elite_model_seeds()
    champion = load_champion()
    if champion is not None:
        seeds.insert(0, champion[0])
    return seeds


def mutate_model(parent: ModelGenes, rng: random.Random) -> ModelGenes:
    child = random_model_genes(rng)
    max_depth = child.max_depth if rng.random() < 0.35 else parent.max_depth
    leaves = child.num_leaves if rng.random() < 0.35 else parent.num_leaves
    indicator_genes = (child.indicators, child.windows) if rng.random() < 0.35 else (parent.indicators, parent.windows)
    return ModelGenes(
        horizon=child.horizon if rng.random() < 0.30 else parent.horizon,
        indicators=indicator_genes[0],
        windows=indicator_genes[1],
        learning_rate=child.learning_rate if rng.random() < 0.35 else parent.learning_rate,
        max_depth=max_depth,
        num_leaves=max(2, min(leaves, 2**max_depth)),
        min_child_samples=child.min_child_samples if rng.random() < 0.35 else parent.min_child_samples,
        feature_fraction=child.feature_fraction if rng.random() < 0.35 else parent.feature_fraction,
        bagging_fraction=child.bagging_fraction if rng.random() < 0.35 else parent.bagging_fraction,
        reg_alpha=child.reg_alpha if rng.random() < 0.35 else parent.reg_alpha,
        reg_lambda=child.reg_lambda if rng.random() < 0.35 else parent.reg_lambda,
        n_estimators=child.n_estimators if rng.random() < 0.35 else parent.n_estimators,
    )


def candidate_features(genes: ModelGenes) -> list[str]:
    base = [
        "coin",
        "market_return",
        "market_momentum",
        "market_breadth",
        *[f"base_ret_{window}" for window in (1, 2, 3, 6, 12, 24, 48)],
        *[f"base_vol_{window}" for window in (6, 12, 24, 48)],
        *[f"base_volume_ratio_{window}" for window in (6, 12, 24, 48)],
        # 横截面相对特征 (对抗 coin 主导)
        *[f"excess_ret_{window}" for window in (1, 2, 3, 6, 12, 24, 48)],
        *[f"cs_rank_{window}" for window in (1, 2, 3, 6, 12, 24, 48)],
        "rel_volume",
        # 微结构特征 (蜡烛形态/量价)
        "micro_body", "micro_upper_wick", "micro_lower_wick",
        "micro_intraday", "micro_range", "micro_gap", "micro_vol_chg",
        *[f"micro_body_{w}" for w in MICRO_WINDOWS],
        *[f"micro_intraday_{w}" for w in MICRO_WINDOWS],
        *[f"micro_vol_chg_{w}" for w in MICRO_WINDOWS],
    ]
    selected: list[str] = []
    for indicator, window in zip(genes.indicators, genes.windows, strict=True):
        if indicator == "obv":
            selected.append("ta_obv_1")
        elif indicator in {"adx", "mfi"} and window < 6:
            selected.append(f"ta_{indicator}_6")
        else:
            selected.append(f"ta_{indicator}_{window}")
    return list(dict.fromkeys([*base, *selected]))


def strategy_context_features() -> list[str]:
    indicators = ("rsi", "macd", "adx", "stoch", "donchian", "roc", "cci", "keltner", "atr")
    return list(dict.fromkeys(
        [
        f"ta_{indicator}_{max(6, window) if indicator == 'adx' else window}"
        for indicator in indicators
        for window in WINDOWS
        ]
    ))


def build_panel(coins: dict[str, pd.DataFrame], horizon: int) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for frame in coins.values():
        part = frame.copy()
        entry = part["open"].shift(-1)
        exit_price = part["open"].shift(-(horizon + 1))
        future_high = part["high"].shift(-1).iloc[::-1].rolling(MAX_HOLDING_BARS, min_periods=1).max().iloc[::-1]
        future_low = part["low"].shift(-1).iloc[::-1].rolling(MAX_HOLDING_BARS, min_periods=1).min().iloc[::-1]
        part["future_gross_return"] = exit_price / entry - 1.0
        part["future_max_return"] = future_high / entry - 1.0
        part["future_min_return"] = future_low / entry - 1.0
        part.loc[exit_price.isna(), "future_gross_return"] = np.nan
        part.loc[entry.isna(), ["future_max_return", "future_min_return"]] = np.nan
        parts.append(part)
    panel = pd.concat(parts, ignore_index=True).sort_values(["timestamp", "coin"]).reset_index(drop=True)
    if TARGET_MODE == "regression":
        panel["target"] = panel["future_gross_return"].astype("float32")
    else:
        threshold = panel.groupby("timestamp")["future_gross_return"].transform("quantile", 0.70)
        panel["target"] = (panel["future_gross_return"] >= threshold).astype("float32")
    panel.loc[panel["future_gross_return"].isna(), "target"] = np.nan
    return panel


def make_folds(timestamps: pd.DatetimeIndex, horizon: int) -> tuple[list[Fold], pd.Timestamp]:
    unique = pd.DatetimeIndex(sorted(timestamps.unique()))
    if FINAL_CUTOFF:
        cutoff_ts = pd.Timestamp(FINAL_CUTOFF, tz="UTC")
        later = unique[unique >= cutoff_ts]
        final_start = later[0] if len(later) else unique[-1]
    else:
        final_start = unique[int(len(unique) * (1.0 - FINAL_FRACTION))]
    selection = unique[unique < final_start]
    folds: list[Fold] = []
    for fold_id, (train_ratio, valid_ratio) in enumerate(FOLD_RATIOS):
        train_index = max(1, int(len(selection) * train_ratio) - horizon)
        valid_start_index = int(len(selection) * train_ratio)
        valid_end_index = int(len(selection) * valid_ratio)
        folds.append(Fold(fold_id, selection[train_index], selection[valid_start_index], selection[valid_end_index]))
    return folds, final_start


def normalize_coin_categories(train: pd.DataFrame, valid: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    levels = sorted(train["coin"].unique())
    train_copy, valid_copy = train.copy(), valid.copy()
    train_copy["coin"] = pd.Categorical(train_copy["coin"], categories=levels)
    valid_copy["coin"] = pd.Categorical(valid_copy["coin"], categories=levels)
    return train_copy, valid_copy


def train_model(
    train: pd.DataFrame,
    valid: pd.DataFrame,
    features: list[str],
    genes: ModelGenes,
    gpu: bool,
    boost_rounds: int | None = None,
    early_stopping: bool = True,
) -> Booster:
    import lightgbm as lgb
    from lightgbm.basic import LightGBMError

    params = {
        "objective": "regression" if TARGET_MODE == "regression" else "binary",
        "metric": (["l2", "rmse"] if TARGET_MODE == "regression" else ["auc", "binary_logloss"]),
        "learning_rate": genes.learning_rate,
        "max_depth": genes.max_depth,
        "num_leaves": genes.num_leaves,
        "min_data_in_leaf": genes.min_child_samples,
        "feature_fraction": genes.feature_fraction,
        "bagging_fraction": genes.bagging_fraction,
        "bagging_freq": 1,
        "lambda_l1": genes.reg_alpha,
        "lambda_l2": genes.reg_lambda,
        "path_smooth": 1.0,
        "cat_smooth": 10.0,
        "seed": SEED,
        "feature_fraction_seed": SEED,
        "bagging_seed": SEED,
        "verbosity": -1,
        "device": "gpu" if gpu else "cpu",
        "gpu_platform_id": 0 if gpu else -1,
        "gpu_device_id": 0 if gpu else -1,
        "max_bin": 63 if gpu else 255,
        "force_col_wise": not gpu,
        "num_threads": NUM_THREADS,
    }
    dataset = lgb.Dataset(train[features], label=train["target"], categorical_feature=["coin"])
    stop = lgb.Dataset(valid[features], label=valid["target"], categorical_feature=["coin"], reference=dataset)
    callbacks = [lgb.log_evaluation(0)]
    valid_sets = [stop] if early_stopping else None
    if early_stopping:
        callbacks.append(lgb.early_stopping(60, verbose=False))
    try:
        return lgb.train(params, dataset, num_boost_round=boost_rounds or genes.n_estimators, valid_sets=valid_sets, callbacks=callbacks)
    except LightGBMError:
        if gpu:
            return train_model(
                train,
                valid,
                features,
                genes,
                gpu=False,
                boost_rounds=boost_rounds,
                early_stopping=early_stopping,
            )
        raise


def binary_auc(labels: np.ndarray, probabilities: np.ndarray) -> float:
    positive = labels == 1
    positive_count = int(positive.sum())
    negative_count = len(labels) - positive_count
    if min(positive_count, negative_count) == 0:
        return 0.5
    ranks = pd.Series(probabilities).rank(method="average").to_numpy()
    return float((ranks[positive].sum() - positive_count * (positive_count + 1) / 2) / (positive_count * negative_count))


def prediction_metrics(frame: pd.DataFrame) -> dict[str, float]:
    if TARGET_MODE == "regression":
        auc = binary_auc(
            (frame["target"] > frame["target"].median()).to_numpy(),
            frame["probability"].to_numpy(),
        )
    else:
        auc = binary_auc(frame["target"].to_numpy(), frame["probability"].to_numpy())
    rank_ic = frame.groupby("timestamp", observed=True).apply(
        lambda group: group["probability"].corr(group["future_gross_return"], method="spearman"),
        include_groups=False,
    ).dropna()
    return {"auc": auc, "rank_ic": float(rank_ic.mean()) if not rank_ic.empty else 0.0}


def backtest_oof(frame: pd.DataFrame, strategy: StrategyGenes) -> dict[str, float | int]:
    groups = list(frame.groupby("timestamp", sort=True, observed=True))
    gross_returns: list[float] = []
    risk_adjusted_returns: list[float] = []
    net_returns: list[float] = []
    previous_weights: dict[str, float] = {}
    trades = 0
    for index, (_, group) in enumerate(groups):
        if index % strategy.rebalance_every != 0:
            continue
        if group.empty or float(group["market_momentum"].iloc[0]) < strategy.market_gate:
            gross_returns.append(0.0)
            net_returns.append(0.0)
            continue
        cutoff = float(group["probability"].quantile(strategy.quantile))
        selected = group.loc[group["probability"] >= cutoff].nlargest(strategy.top_k, "probability")
        if selected.empty:
            gross_returns.append(0.0)
            net_returns.append(0.0)
            continue
        gross_return = float(selected["future_gross_return"].mean())
        gross_returns.append(gross_return)
        net_returns.append(gross_return - ROUND_TRIP_COST)
        trades += len(selected)
    series = pd.Series(net_returns, dtype="float64")
    gross_series = pd.Series(gross_returns, dtype="float64")
    equity = (1.0 + series).cumprod()
    total_return = float(equity.iloc[-1] - 1.0) if not equity.empty else -1.0
    drawdown = float(((equity.cummax() - equity) / equity.cummax()).max()) if not equity.empty else 1.0
    sharpe = 0.0 if len(series) < 2 or series.std() <= 1e-12 else float(series.mean() / series.std() * np.sqrt(len(series)))
    gross_equity = (1.0 + gross_series).cumprod()
    gross_return = float(gross_equity.iloc[-1] - 1.0) if not gross_equity.empty else -1.0
    return {
        "return": total_return,
        "gross_return": gross_return,
        "cost_drag": gross_return - total_return,
        "sharpe": sharpe,
        "drawdown": drawdown,
        "trades": trades,
    }


def random_strategy(rng: random.Random, horizon: int) -> StrategyGenes:
    rebalance_choices = tuple(interval for interval in HIGH_FREQUENCY_INTERVALS if interval >= horizon)
    return StrategyGenes(
        quantile=rng.choice((0.70, 0.75, 0.80, 0.85, 0.90)),
        top_k=rng.choice((1, 2, 3, 5)),
        rebalance_every=rng.choice(rebalance_choices),
        market_gate=rng.choice((-0.01, 0.0, 0.005, 0.01, 0.02)),
    )


def backtest_chromosome(
    frame: pd.DataFrame,
    chromosome: StrategyChromosome,
    model_horizon: int,
) -> dict[str, float | int]:
    chromosome = repair_strategy_chromosome(chromosome, model_horizon)
    groups = list(frame.groupby("timestamp", sort=True, observed=True))
    gross_returns: list[float] = []
    risk_adjusted_returns: list[float] = []
    net_returns: list[float] = []
    previous_weights: dict[str, float] = {}
    trades = 0
    signal_return = 0.0
    risk_drag = 0.0
    execution_drag = 0.0
    candidate_count = 0
    filtered_count = 0
    active_rebalances = 0
    market_gate_rejections = 0
    breadth_gate_rejections = 0
    signal_rejections = 0
    scheduled_rebalances = 0
    total_turnover = 0.0
    max_turnover = 0.0
    stop_loss_hits = 0
    take_profit_hits = 0
    last_trade_index: dict[str, int] = {}
    for index, (_, group) in enumerate(groups):
        if index % chromosome.execution.rebalance_bars != 0:
            continue
        scheduled_rebalances += 1
        if float(group["market_momentum"].iloc[0]) < chromosome.market_gate:
            market_gate_rejections += 1
            gross_returns.append(0.0)
            net_returns.append(0.0)
            continue
        if float(group["market_breadth"].iloc[0]) < chromosome.breadth_gate:
            breadth_gate_rejections += 1
            gross_returns.append(0.0)
            net_returns.append(0.0)
            continue
        probability_cutoff = float(group["probability"].quantile(chromosome.quantile))
        selected = group.loc[group["probability"] >= probability_cutoff].nlargest(
            chromosome.top_k,
            "probability",
        )
        candidate_count += len(selected)
        if selected.empty:
            gross_returns.append(0.0)
            net_returns.append(0.0)
            continue
        confirmation_window = max(6, chromosome.signal.confirmation_window) if chromosome.signal.confirmation_indicator == "adx" else chromosome.signal.confirmation_window
        confirmation_column = f"ta_{chromosome.signal.confirmation_indicator}_{confirmation_window}"
        if chromosome.signal.mode in {"momentum_confirmation", "mean_reversion_confirmation"}:
            confirmation = selected[confirmation_column]
            if chromosome.signal.confirmation_indicator in {"rsi", "stoch", "donchian", "keltner"}:
                confirmation = confirmation - 0.5
            direction = chromosome.signal.confirmation_direction
            if chromosome.signal.mode == "mean_reversion_confirmation":
                direction *= -1
            selected = selected.loc[confirmation * direction > 0.0]
        elif chromosome.signal.mode == "turtle_breakout":
            turtle_column = f"ta_donchian_{chromosome.signal.confirmation_window}"
            selected = selected.loc[selected[turtle_column] >= 0.90]
        elif chromosome.signal.mode == "trend_following":
            adx_window = max(6, chromosome.signal.confirmation_window)
            selected = selected.loc[
                (selected[f"ta_adx_{adx_window}"] >= 0.20)
                & (selected["base_ret_24"] * chromosome.signal.confirmation_direction > 0.0)
                & (selected["base_ret_48"] * chromosome.signal.confirmation_direction > 0.0)
            ]
        if chromosome.signal.mode in {"model_rank", "momentum_confirmation", "mean_reversion_confirmation"}:
            selected = selected.loc[selected["probability"] >= chromosome.signal.threshold]
        if chromosome.risk.cooldown_bars > 0:
            cooldown_mask = [
                index - last_trade_index.get(str(coin), -10**9) >= chromosome.risk.cooldown_bars
                for coin in selected["coin"]
            ]
            selected = selected.loc[cooldown_mask]
        filtered_count += len(selected)
        if selected.empty:
            signal_rejections += 1
            gross_returns.append(0.0)
            net_returns.append(0.0)
            continue
        selected = selected.loc[selected["future_gross_return"].notna()]
        if selected.empty:
            signal_rejections += 1
            gross_returns.append(0.0)
            net_returns.append(0.0)
            continue
        if chromosome.risk.sizing_mode == "score_weighted":
            scores = selected["probability"].clip(lower=0.0)
            weights = scores / max(float(scores.sum()), 1e-12)
        elif chromosome.risk.sizing_mode == "inverse_vol":
            volatility = selected.get("base_vol_24", pd.Series(1.0, index=selected.index)).clip(lower=1e-6)
            inverse_vol = 1.0 / volatility
            weights = inverse_vol / max(float(inverse_vol.sum()), 1e-12)
        else:
            weights = pd.Series(1.0 / len(selected), index=selected.index)
        target_total = min(1.0 - chromosome.risk.cash_buffer, chromosome.risk.max_position * len(selected))
        target_weights = {
            str(coin): float(weight * target_total)
            for coin, weight in zip(selected["coin"], weights)
        }
        all_coins = set(previous_weights) | set(target_weights)
        turnover = sum(
            abs(target_weights.get(coin, 0.0) - previous_weights.get(coin, 0.0))
            for coin in all_coins
        )
        if turnover > chromosome.execution.turnover_cap:
            scale = chromosome.execution.turnover_cap / turnover
            target_weights = {
                coin: previous_weights.get(coin, 0.0)
                + (weight - previous_weights.get(coin, 0.0)) * scale
                for coin, weight in target_weights.items()
            }
            turnover = chromosome.execution.turnover_cap
        previous_weights = target_weights
        total_turnover += turnover
        max_turnover = max(max_turnover, turnover)
        position_returns = selected["future_gross_return"].to_numpy(dtype="float64")
        stop_returns = selected["future_min_return"].to_numpy(dtype="float64")
        take_returns = selected["future_max_return"].to_numpy(dtype="float64")
        # 动态止盈止损: ATR 模式按 24-bar ATR 自适应; fixed 用固定百分比
        if chromosome.risk.sl_tp_mode == "atr":
            atr_col = "ta_atr_24"
            if atr_col in selected.columns:
                atr = selected[atr_col].to_numpy(dtype="float64")
                atr = np.where(np.isfinite(atr) & (atr > 0), atr, 0.02)
            else:
                atr = np.full(len(selected), 0.02)
            stop_loss_pct = chromosome.risk.stop_loss * atr
            take_profit_pct = chromosome.risk.take_profit * atr
        else:
            stop_loss_pct = np.full(len(selected), chromosome.risk.stop_loss)
            take_profit_pct = np.full(len(selected), chromosome.risk.take_profit)
        stop_hit = stop_returns <= -stop_loss_pct
        take_hit = take_returns >= take_profit_pct
        stop_loss_hits += int(stop_hit.sum())
        take_profit_hits += int(take_hit.sum())
        trailing_hit = (
            (take_returns > 0.0)
            & ((take_returns - position_returns) >= chromosome.risk.trailing_stop)
        )
        realized_returns = np.where(
            stop_hit,
            -stop_loss_pct,
            np.where(
                take_hit,
                take_profit_pct,
                np.where(trailing_hit, take_returns - chromosome.risk.trailing_stop, position_returns),
            ),
        )
        position_weights = np.asarray([target_weights[str(coin)] for coin in selected["coin"]])
        gross_return = float(np.dot(realized_returns, position_weights))
        risk_penalty = max(0.0, abs(gross_return) - chromosome.risk.volatility_cap) * 0.25
        risk_adjusted_return = gross_return - risk_penalty
        cost = turnover * (chromosome.execution.fee_stress + chromosome.execution.slippage_stress)
        net_return = risk_adjusted_return - cost
        gross_returns.append(gross_return)
        risk_adjusted_returns.append(risk_adjusted_return)
        net_returns.append(net_return)
        signal_return = float(np.prod(1.0 + np.asarray(gross_returns)) - 1.0)
        risk_drag = signal_return - float(np.prod(1.0 + np.asarray(risk_adjusted_returns)) - 1.0)
        execution_drag += cost
        trades += len(selected)
        active_rebalances += 1
        for coin in selected["coin"]:
            last_trade_index[str(coin)] = index
    series = pd.Series(net_returns, dtype="float64")
    equity = (1.0 + series).cumprod()
    total_return = float(equity.iloc[-1] - 1.0) if not equity.empty else 0.0
    drawdown = float(((equity.cummax() - equity) / equity.cummax()).max()) if not equity.empty else 1.0
    sharpe = 0.0 if len(series) < 2 or series.std() <= 1e-12 else float(series.mean() / series.std() * np.sqrt(len(series)))
    return {
        "return": total_return,
        "signal_return": signal_return,
        "risk_drag": risk_drag,
        "execution_drag": signal_return - risk_drag - total_return,
        "sharpe": sharpe,
        "drawdown": drawdown,
        "trades": trades,
        "candidate_count": candidate_count,
        "filtered_count": filtered_count,
        "active_rebalances": active_rebalances,
        "scheduled_rebalances": scheduled_rebalances,
        "market_gate_rejections": market_gate_rejections,
        "breadth_gate_rejections": breadth_gate_rejections,
        "signal_rejections": signal_rejections,
        "total_turnover": total_turnover,
        "max_turnover": max_turnover,
        "stop_loss_hits": stop_loss_hits,
        "take_profit_hits": take_profit_hits,
    }


def ensure_oof_folds(result: pd.DataFrame) -> pd.DataFrame:
    if "fold" in result.columns:
        return result
    # fold is always assigned upstream by make_folds() in evaluate_model_candidate()
    # with real FOLD_RATIOS (e.g. 0.40/0.50). This is only a fallback guard: equal-width
    # quintiles would silently mismatch the training folds, so fail loudly instead.
    if result.empty or "timestamp" not in result.columns:
        raise DatasetContractError("OOF predictions are empty or missing timestamp")
    raise DatasetContractError(
        "OOF predictions missing 'fold' column — folds are always assigned upstream; "
        "a missing column indicates an upstream bug, not a recoverable condition"
    )


def strategy_fitness(frame: pd.DataFrame, genes: StrategyChromosome, model_horizon: int) -> tuple[float, dict[str, float | int]]:
    fold_metrics = [backtest_chromosome(group, genes, model_horizon) for _, group in frame.groupby("fold", observed=True)]
    returns = [float(metric["return"]) for metric in fold_metrics]
    sharpes = [float(metric["sharpe"]) for metric in fold_metrics]
    drawdowns = [float(metric["drawdown"]) for metric in fold_metrics]
    trades = [int(metric["trades"]) for metric in fold_metrics]
    frequency = float(np.mean([metric["trades"] for metric in fold_metrics]))
    active = [int(metric["active_rebalances"]) for metric in fold_metrics]
    scheduled = [max(1, int(metric["scheduled_rebalances"])) for metric in fold_metrics]
    per_fold_frequency = [a / s for a, s in zip(active, scheduled)]
    active_frequency = float(np.mean(per_fold_frequency))
    min_active_frequency = float(min(per_fold_frequency))
    stop_loss_hits = sum(int(metric["stop_loss_hits"]) for metric in fold_metrics)
    take_profit_hits = sum(int(metric["take_profit_hits"]) for metric in fold_metrics)
    total_trades = max(1, sum(trades))
    stop_ratio = stop_loss_hits / total_trades
    take_ratio = take_profit_hits / total_trades
    score = (
        10.0 * float(np.median(sharpes))
        + 6.0 * min(sharpes)
        + 3.0 * float(np.median(returns))
        + 3.0 * (take_ratio - stop_ratio)
        - 5.0 * max(drawdowns)
        - 3.0 * max(0.0, float(np.median([m["total_turnover"] for m in fold_metrics])) - 0.5)
        - 3.0 * float(np.std(returns))
        + 2.0 * active_frequency
        + 1.0 * min_active_frequency
        - 2.0 * max(0.0, stop_ratio - 0.35)
    )
    if min(trades) == 0 or active_frequency < 0.30:
        score = -1_000_000.0
    elif min(trades) < 12:
        score -= 10.0
    summary: dict[str, float | int] = {
        "median_return": float(np.median(returns)),
        "worst_return": min(returns),
        "median_sharpe": float(np.median(sharpes)),
        "max_drawdown": max(drawdowns),
        "min_trades": min(trades),
        "frequency": frequency,
        "active_frequency": active_frequency,
        "stop_ratio": stop_ratio,
        "take_ratio": take_ratio,
        "zero_trade_folds": int(sum(trade_count == 0 for trade_count in trades)),
    }
    return score, summary


def evolve_strategy(
    oof: pd.DataFrame,
    rng: random.Random,
    horizon: int,
) -> tuple[StrategyChromosome, list[dict[str, float | int]]]:
    population = elite_strategy_seeds(horizon) + [random_strategy_chromosome(rng, horizon) for _ in range(STRATEGY_POPULATION - 5)]
    history: list[dict[str, float | int]] = []
    best_genes = population[0]
    best_score = -np.inf
    hall_of_fame: list[tuple[float, StrategyChromosome]] = []
    stagnation = 0
    for generation in range(1, STRATEGY_GENERATIONS + 1):
        if _HAS_JAX:
            evaluated = jax_strategy_fitness_batch(oof, population, horizon)
            evaluated = [(eval_tuple, genes) for eval_tuple, genes in zip(evaluated, population)]
        else:
            evaluated = [(strategy_fitness(oof, genes, horizon), genes) for genes in population]
        evaluated.sort(key=lambda item: item[0][0], reverse=True)
        (generation_score, metrics), generation_best = evaluated[0]
        if generation_score > best_score:
            best_score, best_genes = generation_score, generation_best
            stagnation = 0
        else:
            stagnation += 1
        hall_of_fame.extend((score, genes) for (score, _), genes in evaluated[:6])
        unique_hof: dict[str, tuple[float, StrategyChromosome]] = {}
        for score, genes in hall_of_fame:
            unique_hof[stable_digest(genes)] = (score, genes)
        hall_of_fame = sorted(unique_hof.values(), key=lambda item: item[0], reverse=True)[:12]
        history.append({"generation": generation, "fitness": generation_score, **metrics})
        elites = [genes for _, genes in evaluated[:6]] + [genes for _, genes in hall_of_fame[:3]]
        population = elites.copy()
        while len(population) < STRATEGY_POPULATION:
            parent = rng.choice(elites)
            mutation = random_strategy_chromosome(rng, horizon)
            risk = mutation.risk if rng.random() < 0.30 else parent.risk
            # 变异: 随机切换 fixed/atr 模式, 或按当前模式调整倍数/百分比
            if rng.random() < 0.15:
                risk = replace(risk, sl_tp_mode="atr" if risk.sl_tp_mode == "fixed" else "fixed")
            if risk.sl_tp_mode == "atr":
                if rng.random() < 0.35:
                    risk = replace(
                        risk,
                        stop_loss=round(min(max(risk.stop_loss * rng.uniform(0.7, 1.4), 0.5), 3.0), 2),
                    )
                if rng.random() < 0.35:
                    risk = replace(
                        risk,
                        take_profit=round(min(max(risk.take_profit * rng.uniform(0.7, 1.4), 1.0), 5.0), 2),
                    )
            else:
                if rng.random() < 0.35:
                    risk = replace(
                        risk,
                        stop_loss=round(min(max(risk.stop_loss * rng.uniform(0.6, 1.6), 0.005), 0.06), 3),
                    )
                if rng.random() < 0.35:
                    risk = replace(
                        risk,
                        take_profit=round(min(max(risk.take_profit * rng.uniform(0.6, 1.6), 0.01), 0.12), 3),
                    )
            if rng.random() < 0.25:
                risk = replace(
                    risk,
                    trailing_stop=round(min(max(risk.trailing_stop * rng.uniform(0.5, 1.8), 0.0), 0.05), 3),
                )
            population.append(
                repair_strategy_chromosome(
                    StrategyChromosome(
                        signal=mutation.signal if rng.random() < 0.30 else parent.signal,
                        quantile=mutation.quantile if rng.random() < 0.30 else parent.quantile,
                        top_k=mutation.top_k if rng.random() < 0.30 else parent.top_k,
                        market_gate=mutation.market_gate if rng.random() < 0.30 else parent.market_gate,
                        breadth_gate=mutation.breadth_gate if rng.random() < 0.30 else parent.breadth_gate,
                        risk=risk,
                        execution=mutation.execution if rng.random() < 0.30 else parent.execution,
                    ),
                    horizon,
                )
            )
        if stagnation >= 12:
            population[-5:] = [
                *[random_strategy_chromosome(rng, horizon) for _ in range(3)],
                *[genes for _, genes in hall_of_fame[:2]],
            ]
            stagnation = 0
    (OUTPUT_DIR / "strategy_hall_of_fame.json").write_text(
        json.dumps([{"fitness": score, "strategy": asdict(genes)} for score, genes in hall_of_fame], indent=2),
        encoding="utf-8",
    )
    return best_genes, history


def _pool_init() -> None:
    try:
        import lightgbm  # noqa: F401
    except ModuleNotFoundError:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "lightgbm"],
            check=True,
        )


def _loose_scoring_strategy(model_horizon: int) -> StrategyChromosome:
    """Rank-driven scoring strategy for model GA fitness (inlined).

    threshold=0 so selection is purely rank-based (top_k coins per
    timestamp by probability) — works for weak models whose probability
    magnitudes are flat.
    """
    strat = StrategyChromosome(
        signal=SignalGenes("model_rank", 0.0, "macd", 12, 1),
        quantile=0.50,
        top_k=3,
        market_gate=-0.02,
        breadth_gate=0.20,
        risk=RiskGenes("score_weighted", 3, "fixed", 0.02, 0.06, 0.01, 1, 0.4, 0.10, 0.15),
        execution=ExecutionGenes(3, 0.20, FEE, SLIPPAGE),
    )
    return repair_strategy_chromosome(strat, model_horizon)


def _backtest_oof_returns(
    oof: pd.DataFrame,
    strategy: StrategyChromosome,
    model_horizon: int,
) -> dict:
    """Sharpe-first fitness over per-fold OOF backtests (inlined).

    fold-consistency penalty resists overfitting to a single favorable fold.
    """
    strategy = repair_strategy_chromosome(strategy, model_horizon)
    fold_metrics = [
        backtest_chromosome(group, strategy, model_horizon)
        for _, group in oof.groupby("fold", observed=True)
    ]
    returns = [float(m["return"]) for m in fold_metrics]
    drawdowns = [float(m["drawdown"]) for m in fold_metrics]
    trades = [int(m["trades"]) for m in fold_metrics]
    sharpes = [float(m["sharpe"]) for m in fold_metrics]
    total_turnover = [float(m["total_turnover"]) for m in fold_metrics]

    median_return = float(np.median(returns))
    worst_return = min(returns)
    max_dd = max(drawdowns)
    median_sharpe = float(np.median(sharpes))
    min_fold_sharpe = min(sharpes)
    min_trades = min(trades)
    median_turnover = float(np.median(total_turnover))
    fold_std = float(np.std(returns))

    score = (
        12.0 * median_sharpe
        + 8.0 * min_fold_sharpe
        - 6.0 * max_dd
        - 10.0 * median_turnover
        - 5.0 * fold_std
    )
    if min_trades < 5:
        score -= 2.0 * (5 - min_trades)
    if min_trades == 0:
        score = -1_000_000.0

    return {
        "score": float(score),
        "median_return": median_return,
        "worst_return": worst_return,
        "median_sharpe": median_sharpe,
        "min_fold_sharpe": min_fold_sharpe,
        "median_drawdown": float(np.median(drawdowns)),
        "max_drawdown": max_dd,
        "min_trades": min_trades,
        "median_turnover": median_turnover,
        "fold_return_std": fold_std,
        "fold_returns": returns,
    }


def _train_oof_only(
    genes: ModelGenes,
    coins: dict[str, pd.DataFrame],
    gpu: bool,
) -> pd.DataFrame:
    """Train one model per fold and return the OOF probability frame.

    Lightweight twin of evaluate_model_candidate: skips the strategy-backtest
    fitness (score) and per-fold AUC/IC metrics, which are not needed when we
    only want OOF probabilities for ensembling.
    """
    panel = build_panel(coins, genes.horizon)
    features = candidate_features(genes)
    panel = panel.dropna(subset=[*features, "target", "future_gross_return"])
    folds, _ = make_folds(pd.DatetimeIndex(panel["timestamp"]), genes.horizon)
    context = ["market_breadth", "base_ret_24", *strategy_context_features()]
    predictions: list[pd.DataFrame] = []
    for fold in folds:
        train = panel.loc[panel["timestamp"] < fold.train_end]
        valid = panel.loc[(panel["timestamp"] >= fold.valid_start) & (panel["timestamp"] < fold.valid_end)]
        train, valid = normalize_coin_categories(train, valid)
        model = train_model(train, valid, features, genes, gpu)
        output = valid[["timestamp", "coin", "target", "future_gross_return", "future_max_return", "future_min_return", "market_momentum", *context]].copy()
        output["probability"] = model.predict(valid[features], num_iteration=model.best_iteration)
        output["fold"] = fold.fold_id
        predictions.append(output)
    return pd.concat(predictions, ignore_index=True)


def ensemble_oof(
    top_genes: list[ModelGenes],
    coins: dict[str, pd.DataFrame],
    gpu: bool,
) -> pd.DataFrame:
    """Average Top-K OOF probabilities into one ensemble OOF.

    Fold valid segments are horizon-independent (make_folds valid_start/valid_end
    use only ratios), so rows align on (timestamp, coin). Probability is the mean
    over the models that produced a prediction for that row; all other columns
    come from the first (best) OOF. Reduces single-model probability variance.
    """
    oofs = [_train_oof_only(genes, coins, gpu) for genes in top_genes]
    base = oofs[0].copy()
    align = base[["timestamp", "coin"]]
    prob_sum = np.zeros(len(base), dtype="float64")
    count = np.zeros(len(base), dtype="float64")
    for oof in oofs:
        # 折叠 valid 段可能重叠 → 同一 (timestamp, coin) 出现多次, 先按均值去重
        oof_dedup = oof.groupby(["timestamp", "coin"], as_index=False)["probability"].mean()
        merged = align.merge(oof_dedup, on=["timestamp", "coin"], how="left", suffixes=("", "_r"))
        present = merged["probability"].notna().to_numpy()
        prob_sum[present] += merged.loc[present, "probability"].to_numpy(dtype="float64")
        count[present] += 1.0
    prob_sum[count > 0] /= np.maximum(count[count > 0], 1.0)
    base["probability"] = prob_sum.astype("float32")
    return base


def evaluate_model_candidate(
    genes: ModelGenes,
    coins: dict[str, pd.DataFrame],
    gpu: bool,
) -> tuple[float, pd.DataFrame, list[dict[str, float]], pd.Timestamp]:
    panel = build_panel(coins, genes.horizon)
    features = candidate_features(genes)
    panel = panel.dropna(subset=[*features, "target", "future_gross_return"])
    folds, final_start = make_folds(pd.DatetimeIndex(panel["timestamp"]), genes.horizon)
    predictions: list[pd.DataFrame] = []
    metrics: list[dict[str, float]] = []
    for fold in folds:
        train = panel.loc[panel["timestamp"] < fold.train_end]
        valid = panel.loc[(panel["timestamp"] >= fold.valid_start) & (panel["timestamp"] < fold.valid_end)]
        train, valid = normalize_coin_categories(train, valid)
        model = train_model(train, valid, features, genes, gpu)
        context = ["market_breadth", "base_ret_24", *strategy_context_features()]
        output = valid[["timestamp", "coin", "target", "future_gross_return", "future_max_return", "future_min_return", "market_momentum", *context]].copy()
        output["probability"] = model.predict(valid[features], num_iteration=model.best_iteration)
        output["fold"] = fold.fold_id
        predictions.append(output)
        fold_metric = prediction_metrics(output)
        fold_metric["best_iteration"] = float(model.best_iteration)
        metrics.append(fold_metric)
    oof = pd.concat(predictions, ignore_index=True)
    median_auc = float(np.median([metric["auc"] for metric in metrics]))
    worst_auc = min(metric["auc"] for metric in metrics)
    ic_values = [float(metric["rank_ic"]) for metric in metrics]
    median_ic = float(np.median(ic_values))
    worst_ic = min(ic_values)
    ic_dispersion = float(np.std(ic_values))

    if MODEL_FITNESS == "return":
        bt = _backtest_oof_returns(oof, _loose_scoring_strategy(genes.horizon), genes.horizon)
        score = float(bt["score"])
        if median_ic <= 0.0:
            score = -1_000_000.0
        for m in metrics:
            m["fitness"] = "return"
    else:
        score = (
            80.0 * (median_auc - 0.5)
            + 40.0 * (worst_auc - 0.5)
            + 30.0 * median_ic
            + 15.0 * worst_ic
            - 10.0 * max(0.0, 0.01 - min(median_ic, 0.0))
            - 20.0 * max(0.0, 0.02 - worst_ic)
            - 15.0 * max(0.0, ic_dispersion - 0.02)
        )
    return score, oof, metrics, final_start


def evolve_models(
    coins: dict[str, pd.DataFrame],
    gpu: bool,
    rng: random.Random,
) -> tuple[ModelGenes, pd.DataFrame, list[dict[str, JsonValue]], pd.Timestamp, list[dict[str, float]], list[ModelGenes]]:
    seeds = elite_model_seeds_with_champion()
    populations = [
        [seeds[island % len(seeds)], *[random_island_genes(rng, island) for _ in range(ISLAND_POPULATION - 1)]]
        for island in range(ISLAND_COUNT)
    ]
    cache: dict[str, tuple[float, list[dict[str, float]], pd.Timestamp]] = {}
    cache_genes: dict[str, ModelGenes] = {}
    history: list[dict[str, JsonValue]] = []
    best_genes = populations[0][0]
    best_oof = pd.DataFrame()
    best_score = -np.inf
    best_final_start = pd.Timestamp.min.tz_localize("UTC")
    best_fold_metrics: list[dict[str, float]] = []
    for generation in range(1, ISLAND_GENERATIONS + 1):
        if len(cache) >= MAX_MODEL_CANDIDATES:
            break
        evaluated: list[tuple[float, ModelGenes, list[dict[str, float]], pd.Timestamp]] = []
        evaluated_islands: list[list[tuple[float, ModelGenes, list[dict[str, float]], pd.Timestamp]]] = []
        for population in populations:
            island_results: list[tuple[float, ModelGenes, list[dict[str, float]], pd.Timestamp]] = []
            # Batch evaluate ALL new candidates in this island
            uncached = [(genes, stable_digest(genes)) for genes in population if stable_digest(genes) not in cache]
            if uncached and len(uncached) > 1:
                import multiprocessing as _mp
                ctx = _mp.get_context("fork") if "fork" in _mp.get_all_start_methods() else _mp.get_context("spawn")
                with ctx.Pool(min(8, cpu_count()), initializer=_pool_init) as pool:
                    results = pool.starmap(
                        evaluate_model_candidate,
                        [(genes, coins, gpu) for genes, _ in uncached],
                    )
                for (genes, key), (score, _, fold_metrics, final_start) in zip(uncached, results):
                    cache[key] = (score, fold_metrics, final_start)
                    cache_genes[key] = genes
            else:
                for genes, key in uncached:
                    score, oof, fold_metrics, final_start = evaluate_model_candidate(genes, coins, gpu)
                    cache[key] = (score, fold_metrics, final_start)
                    cache_genes[key] = genes
                    del oof
            for genes in population:
                key = stable_digest(genes)
                score, fold_metrics, final_start = cache[key]
                result = (score, genes, fold_metrics, final_start)
                island_results.append(result)
                evaluated.append(result)
            evaluated_islands.append(sorted(island_results, key=lambda item: item[0], reverse=True))
        evaluated.sort(key=lambda item: item[0], reverse=True)
        score, generation_best, fold_metrics, final_start = evaluated[0]
        if score > best_score:
            best_score = score
            best_genes = generation_best
            best_final_start = final_start
            best_fold_metrics = fold_metrics
        history.append(
            {
                "generation": generation,
                "islands": [asdict(item[0][1]) for item in evaluated_islands],
                "fitness": score,
                "candidate": asdict(generation_best),
                "fold_metrics": fold_metrics,
                "unique_models": len(cache),
            }
        )
        checkpoint = {"stage": "model_ga", "history": history, "best_genes": asdict(best_genes), "seed": SEED}
        (OUTPUT_DIR / "ga_checkpoint.json").write_text(json.dumps(checkpoint, indent=2), encoding="utf-8")
        migrants = [island[0][1] for island in evaluated_islands]
        populations = [
            [item[1] for item in island[:3]]
            + ([migrants[(index - 1) % ISLAND_COUNT]] if generation % MIGRATION_INTERVAL == 0 else [])
            + [random_island_genes(rng, index) for _ in range(2)]
            + [mutate_model(rng.choice([item[1] for item in island[:3]]), rng) for _ in range(ISLAND_POPULATION - (6 if generation % MIGRATION_INTERVAL == 0 else 5))]
            for index, island in enumerate(evaluated_islands)
        ]
    if len(cache) > MAX_MODEL_CANDIDATES:
        raise ModelBudgetError("Model evaluation budget exceeded")
    _, best_oof, _, _ = evaluate_model_candidate(best_genes, coins, gpu)
    ranked = sorted(cache.items(), key=lambda item: item[1][0], reverse=True)
    top_k_genes = [cache_genes[key] for key, _ in ranked[:ENSEMBLE_K]]
    return best_genes, best_oof, history, best_final_start, best_fold_metrics, top_k_genes


def train_full_model(
    train: pd.DataFrame,
    features: list[str],
    genes: ModelGenes,
    gpu: bool,
    boost_rounds: int,
) -> Booster:
    stop_size = max(1000, int(len(train) * 0.02))
    stop = train.iloc[-stop_size:].copy()
    fit = train.iloc[:-stop_size].copy()
    model = train_model(
        fit,
        stop,
        features,
        genes,
        gpu,
        boost_rounds=boost_rounds,
        early_stopping=False,
    )
    return model


def train_final_and_verify(
    genes: ModelGenes,
    strategy: StrategyChromosome,
    coins: dict[str, pd.DataFrame],
    final_start: pd.Timestamp,
    gpu: bool,
    selected_boost_rounds: int,
) -> tuple[Booster, dict[str, JsonValue], list[str], pd.DataFrame]:
    panel = build_panel(coins, genes.horizon)
    features = candidate_features(genes)
    panel = panel.dropna(subset=[*features, "target", "future_gross_return"])
    timestamps = pd.DatetimeIndex(sorted(panel["timestamp"].unique()))
    final_index = int(timestamps.get_loc(final_start))
    purge_start = timestamps[max(0, final_index - genes.horizon)]
    pre_final = panel.loc[panel["timestamp"] < purge_start]
    final = panel.loc[panel["timestamp"] >= final_start]
    train, final = normalize_coin_categories(pre_final, final)
    levels = train["coin"].cat.categories
    final["coin"] = pd.Categorical(final["coin"], categories=levels)
    model = train_full_model(train, features, genes, gpu, selected_boost_rounds)
    context = ["market_breadth", "base_ret_24", *strategy_context_features()]
    predictions = final[["timestamp", "coin", "target", "future_gross_return", "future_max_return", "future_min_return", "market_momentum", *context]].copy()
    predictions["probability"] = model.predict(final[features], num_iteration=model.best_iteration)
    predictive = prediction_metrics(predictions)
    backtest = backtest_chromosome(predictions, strategy, genes.horizon)
    frequency = float(backtest["active_rebalances"]) / max(1, int(backtest["scheduled_rebalances"]))
    deployable = (
        float(backtest["return"]) > 0.05
        and float(backtest["sharpe"]) > 0.75
        and float(backtest["drawdown"]) <= 0.20
        and selected_boost_rounds >= 16
        and frequency >= 0.25
    )
    report: dict[str, JsonValue] = {
        "predictive": predictive,
        "backtest": backtest,
        "final_start": str(final_start),
        "rows": len(predictions),
        "coins": int(predictions["coin"].nunique()),
        "selected_boost_rounds": selected_boost_rounds,
        "actual_boost_rounds": model.num_trees(),
        "signal_frequency": frequency,
        "auc_target": AUC_TARGET,
        "deployment_status": "DEPLOYABLE_CANDIDATE" if deployable else "NO_DEPLOYABLE_STRATEGY",
        "evaluation_note": "Final chronological segment was excluded from all GA fitness and inspected once after selection.",
        "research_only": True,
    }
    return model, report, features, predictions


def main() -> None:
    if _HAS_JAX:
        print(f"[short-term-ta-ga-v1] jax devices: {jax.devices()}")
        print(f"[short-term-ta-ga-v1] jax platform: {jax.default_backend()}")
    random.seed(SEED)
    np.random.seed(SEED)
    rng = random.Random(SEED)
    run_manifest = {
        "seed": SEED,
        "model_islands": ISLAND_COUNT,
        "model_population_per_island": ISLAND_POPULATION,
        "model_generations": ISLAND_GENERATIONS,
        "strategy_population": STRATEGY_POPULATION,
        "strategy_generations": STRATEGY_GENERATIONS,
        "ensemble": ENSEMBLE,
        "ensemble_k": ENSEMBLE_K,
        "oof_cache_policy": "lightweight_metrics_only",
        "strategy_hall_of_fame_size": 12,
        "elite_genes": "v15_champion_model_and_strategy_seeded",
        "frequency_weight": "active_frequency_6.0_min_active_frequency_3.0",
        "exit_balance": "stop_ratio_penalty_3.0_take_ratio_reward_2.0",
        "v18_fixes": "predictive_model_score_early_stopping_60_boost_floor_64_non_overlap_folds_hold_window_6_threshold_and_cooldown_wired_hof_dedup",
        "v20_fixes": "rebalance_longer_3_6_12_turnover_cap_0.05_0.40_take_profit_0.015_0.060",
        "final_holdout_policy": "single_terminal_evaluation_no_feedback",
    }
    (OUTPUT_DIR / "run_manifest.json").write_text(json.dumps(run_manifest, indent=2), encoding="utf-8")
    ensure_ta()
    ensure_deps()
    gpu = has_gpu()
    print(f"[short-term-ta-ga-v1] gpu={gpu} max_model_candidates={MAX_MODEL_CANDIDATES}")
    print(f"[short-term-ta-ga-v1] jax={_HAS_JAX}")
    coins = load_ohlcv()
    if MAX_TRAIN_COINS > 0 and len(coins) > MAX_TRAIN_COINS:
        coins = {k: coins[k] for k in list(coins)[:MAX_TRAIN_COINS]}
        print(f"[short-term-ta-ga-v1] limited to {MAX_TRAIN_COINS} coins")
    add_base_features(coins)
    add_micro_features(coins)
    add_cross_sectional_features(coins)
    add_ta_bank(coins)
    recovered = checkpoint_candidate()
    top_k_genes: list[ModelGenes] = []
    if recovered is None:
        best_model_genes, oof, model_history, final_start, best_fold_metrics, top_k_genes = evolve_models(coins, gpu, rng)
    else:
        _, oof, best_fold_metrics, final_start = evaluate_model_candidate(recovered, coins, gpu)
        best_model_genes = recovered
        top_k_genes = [recovered]
        model_history = [{"generation": "checkpoint_recovery", "candidate": asdict(recovered), "fold_metrics": best_fold_metrics}]
    if ENSEMBLE and top_k_genes:
        oof = ensemble_oof(top_k_genes, coins, gpu)
        print(f"[short-term-ta-ga-v1] ensemble: {len(top_k_genes)}-model Top-{ENSEMBLE_K} averaged OOF probabilities")
    if oof.empty or "timestamp" not in oof.columns:
        _, oof, best_fold_metrics, final_start = evaluate_model_candidate(best_model_genes, coins, gpu)
    oof = ensure_oof_folds(oof)
    best_strategy, strategy_history = evolve_strategy(oof, rng, best_model_genes.horizon)
    iteration_values = [int(metric["best_iteration"]) for metric in best_fold_metrics]
    selected_boost_rounds = max(64, int(np.median(iteration_values)))
    model, final_report, features, final_predictions = train_final_and_verify(
        best_model_genes,
        best_strategy,
        coins,
        final_start,
        gpu,
        selected_boost_rounds,
    )
    model.save_model(str(OUTPUT_DIR / "short_term_ta_ga_model.txt"), num_iteration=model.best_iteration)
    oof.to_parquet(OUTPUT_DIR / "oof_predictions.parquet", index=False)
    final_predictions.to_parquet(OUTPUT_DIR / "final_predictions.parquet", index=False)
    pd.DataFrame(model_history).to_json(OUTPUT_DIR / "model_ga_history.json", orient="records", indent=2)
    pd.DataFrame(strategy_history).to_csv(OUTPUT_DIR / "strategy_ga_history.csv", index=False)
    split_manifest = {
        "fold_ratios": FOLD_RATIOS,
        "purge_bars": best_model_genes.horizon,
        "final_fraction": FINAL_FRACTION,
        "final_start": str(final_start),
    }
    result = {
        "best_model_chromosome": asdict(best_model_genes),
        "best_strategy_chromosome": asdict(best_strategy),
        "features": features,
        "split_manifest": split_manifest,
        "final_verification": final_report,
        "dataset": {"coins": len(coins), "source_files": len(coins)},
        "costs": {"fee_per_side": FEE, "slippage_per_side": SLIPPAGE},
        "selected_boost_rounds": selected_boost_rounds,
        "seed": SEED,
        "closed_loop": run_manifest,
    }
    (OUTPUT_DIR / "best_chromosome.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (OUTPUT_DIR / "feature_manifest.json").write_text(json.dumps({"features": features}, indent=2), encoding="utf-8")
    (OUTPUT_DIR / "split_manifest.json").write_text(json.dumps(split_manifest, indent=2), encoding="utf-8")
    attribution = {
        "final": final_report["backtest"],
        "strategy": asdict(best_strategy),
        "horizon": best_model_genes.horizon,
        "non_overlapping_rebalance": best_strategy.execution.rebalance_bars >= best_model_genes.horizon,
        "risk_genes": asdict(best_strategy.risk),
        "execution_genes": asdict(best_strategy.execution),
    }
    (OUTPUT_DIR / "strategy_attribution.json").write_text(json.dumps(attribution, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
