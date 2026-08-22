from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from data_feed import LiveFeed
from notify import clear_error_state, configure as smtp_configure
from notify import get_config as smtp_get_config
from notify import notify_error, notify_signal, test_send as smtp_test_send
from paper import PaperTrader
from signals import SignalEngine

STATIC_DIR: Path = Path(__file__).resolve().parent / "static"

live_feed = LiveFeed()
engine = SignalEngine(horizon=3)
paper_trader = PaperTrader()

_lock = threading.Lock()
_stop_event = threading.Event()
_feed_thread: threading.Thread | None = None
_feed_lock = threading.Lock()  # serializes feed restarts
_generation = 0  # bumped on every restart; stale loops exit when it changes

_start_time: float = time.time()  # process start time for health/uptime
EQUITY_HISTORY_LIMIT: int = 500  # capped equity points kept server-side
_equity_history: list[float] = []  # portfolio total_market_cap samples, guarded by _lock

_latest_state: dict = {
    "bars": {},
    "signals": [],
    "portfolio": {},
    "updated_at": None,
    "source": "live",
    "engine_ready": engine.ready,
    "live_source": None,
    "live_error": None,
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> object:
    global _feed_thread
    _feed_thread = threading.Thread(target=_live_loop, daemon=True)
    _feed_thread.start()
    yield
    _stop_event.set()


app = FastAPI(title="Quant Trader Gateway", version="0.1.0", lifespan=lifespan)


def _err(code: str, message: str, status: int) -> JSONResponse:
    """Uniform error envelope: {"error": {"code": ..., "message": ...}}."""
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _append_equity(summary: dict) -> None:
    """Record one equity sample (portfolio total_market_cap) under _lock."""
    if not isinstance(summary, dict):
        return
    cap = summary.get("total_market_cap")
    if cap is None:
        return
    _equity_history.append(float(cap))
    if len(_equity_history) > EQUITY_HISTORY_LIMIT:
        del _equity_history[:-EQUITY_HISTORY_LIMIT]


LIVE_POLL_SECONDS: int = 30
# 全量 200-bar 历史每 2 小时刷新一次 (240 loops x 30s = 2h), 平时只增量拉最近 1 根
LIVE_HISTORY_REFRESH_LOOPS: int = 240


def _notify_live(signals: list[dict], source: str | None, live_error: str | None, model_error: str | None) -> None:
    """Live-mode 通知: 新 BUY 信号 + 数据源/模型错误 (SMTP 未配置时静默 no-op)."""
    notify_signal(signals, source)
    if live_error:
        notify_error(live_error, kind="live")
        return
    if model_error:
        notify_error(model_error, kind="model")
        return
    clear_error_state()


def _live_loop() -> None:
    my_gen = _generation
    first = True
    loop_count = 0
    backoff = LIVE_POLL_SECONDS
    while not _stop_event.is_set() and my_gen == _generation:
        refresh_history = first or loop_count % LIVE_HISTORY_REFRESH_LOOPS == 0
        try:
            candles = live_feed.refresh(
                limit=200 if refresh_history else 1,
                fill_history=refresh_history,
                max_workers=8,
            )
        except Exception as exc:
            with _lock:
                _latest_state["live_error"] = str(exc)
            backoff = min(backoff * 2, 300)
            _stop_event.wait(backoff)
            continue
        backoff = LIVE_POLL_SECONDS
        first = False
        loop_count += 1
        with _lock:
            _latest_state["bars"] = {}
            for coin, candle in candles.items():
                _latest_state["bars"][coin] = {
                    "timestamp": str(candle.timestamp),
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                }
            frames = {}
            for coin in live_feed.coins:
                if coin in live_feed.history_cache:
                    frames[coin] = live_feed.history_cache[coin].reset_index(drop=True)
            signals = engine.signal_weights(frames)
            prices = {
                coin: float(frame.iloc[-1]["close"])
                for coin, frame in frames.items()
                if not frame.empty and "close" in frame.columns
            }
            _latest_state["signals"] = signals
            portfolio_summary = engine.portfolio_summary(frames)
            _latest_state["portfolio"] = portfolio_summary
            _append_equity(portfolio_summary)
            live_source = live_feed.source
            live_error = live_feed.last_error
            _latest_state["live_source"] = live_source
            _latest_state["live_error"] = live_error
            _latest_state["updated_at"] = time.time()
        buy_coins = [s["coin"] for s in signals if s.get("signal") == "BUY"]
        paper_trader.update(buy_coins, prices)
        _notify_live(signals, live_source, live_error, engine._model_error)
        _stop_event.wait(backoff)


@app.post("/api/source")
def restart_feed(payload: dict | None = None) -> dict:
    """重启 live feed 线程 (回放模式已移除, 数据源恒为 live)."""
    global _feed_thread, _generation
    _generation += 1
    _stop_event.set()
    if _feed_thread is not None:
        _feed_thread.join(timeout=10)
    _stop_event.clear()
    with _lock:
        _latest_state["bars"] = {}
        _latest_state["signals"] = []
        _latest_state["portfolio"] = {}
        _latest_state["live_source"] = None
        _latest_state["live_error"] = None
        _latest_state["updated_at"] = time.time()
    _feed_thread = threading.Thread(target=_live_loop, daemon=True)
    _feed_thread.start()
    return {"status": "ok", "feed_mode": "live"}


@app.get("/api/health")
def health() -> dict:
    with _lock:
        updated_at = _latest_state["updated_at"]
    data_age = None if updated_at is None else time.time() - updated_at
    return {
        "status": "ok",
        "engine_ready": engine.ready,
        "model_ready": engine._model_ready,
        "model_error": engine._model_error,
        "coins": len(live_feed.coins),
        "source": "live",
        "feed_mode": "live",
        "uptime_seconds": round(time.time() - _start_time, 1),
        "feed_thread_alive": _feed_thread is not None and _feed_thread.is_alive(),
        "data_age_seconds": None if data_age is None else round(data_age, 1),
        "version": app.version,
    }


@app.get("/api/strategy")
def strategy() -> dict:
    return {
        "engine_ready": engine.ready,
        "model": engine.model_dict,
        "strategy": engine.strategy_dict,
        "features": engine.features,
    }


@app.get("/api/state")
def state() -> dict:
    with _lock:
        out = dict(_latest_state)
    out["feed_mode"] = "live"
    out["model_coverage"] = engine._last_coverage
    return out


@app.get("/api/equity")
def equity() -> dict:
    with _lock:
        history = list(_equity_history)
    return {"equity": history, "count": len(history)}


@app.get("/api/paper")
def paper() -> dict:
    prices: dict[str, float] = {}
    with _lock:
        for coin in live_feed.coins:
            frame = live_feed.history_cache.get(coin)
            if frame is not None and not frame.empty:
                prices[coin] = float(frame.iloc[-1]["close"])
    return paper_trader.summary(prices)


@app.post("/api/paper/reset")
def paper_reset() -> dict:
    """清空模拟仓位: 平掉全部持仓, 清空历史成交, 恢复初始资金."""
    return paper_trader.reset()


@app.get("/api/coins")
def coins() -> dict:
    return {"coins": list(live_feed.coins)}


@app.get("/api/smtp")
def smtp_config() -> dict:
    return smtp_get_config()


@app.post("/api/smtp")
def smtp_save(payload: dict | None) -> dict:
    payload = payload or {}
    cfg = smtp_configure(
        sender=str(payload.get("sender") or ""),
        auth_code=str(payload.get("auth_code") or ""),
        host=str(payload.get("host") or ""),
        port=int(payload.get("port") or 465),
        to=str(payload.get("to") or ""),
    )
    return cfg


@app.post("/api/smtp/test")
def smtp_test() -> dict:
    ok, msg = smtp_test_send()
    return {"ok": ok, "message": msg}


@app.post("/api/smtp/clear")
def smtp_clear() -> dict:
    """清空 SMTP 配置 (从持久化文件删除)."""
    from notify import clear as smtp_clear_all

    return smtp_clear_all()


@app.get("/api/history/{coin}")
def history(coin: str, n: int = Query(200, ge=1, le=500)) -> dict:
    coin = coin.upper()  # normalize lowercase input, e.g. "btc" -> "BTC"
    inst = coin if coin.endswith("-USDT") else f"{coin}-USDT"
    if inst not in live_feed.history_cache:
        return _err(
            "HISTORY_NOT_READY",
            f"{inst} history not fetched yet",
            404,
        )
    frame = live_feed.history_cache[inst].iloc[-n:]
    rows = frame[["timestamp", "open", "high", "low", "close", "volume"]].to_dict("records")
    return {"coin": inst, "rows": rows}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
