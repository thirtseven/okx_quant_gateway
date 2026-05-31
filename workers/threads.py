"""Background threads for OKX Quantitative Trading Gateway."""

import asyncio
import logging
import threading
import time
import traceback
from typing import Any, Dict

import arrow

logger = logging.getLogger(__name__)


def start_engine_bridge(state_ref, state_lock):
    """Poll signal engine and populate indicator_cache for strategy_tick."""
    import httpx

    def engine_bridge():
        while True:
            try:
                r = httpx.get("http://127.0.0.1:5001/api/signals", timeout=10)
                if r.status_code == 200:
                    engine_data = r.json()
                    signals = engine_data.get("signals", {})
                    if signals:
                        pairs = set(state_ref.get("trading_pairs", []))
                        filtered = {k: v for k, v in signals.items() if k in pairs}
                        if filtered:
                            with state_lock:
                                state_ref["indicator_cache"] = filtered
                                state_ref["last_update"] = engine_data.get("last_update", "never")
                            logger.debug("[BRIDGE] Loaded %d signals from engine", len(filtered))
            except Exception:
                pass
            time.sleep(30)

    t = threading.Thread(target=engine_bridge, daemon=True, name="engine-bridge")
    t.start()
    return t


def _run_async_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


def start_data_updater(state_ref: Dict[str, Any], state_lock: threading.Lock, genome: Dict, save_state_func):
    import exchange.client as okx_client
    from core.constants import build_genome
    from core.state import mark_dirty

    def data_updater():
        loop = _run_async_loop()
        time.sleep(10)  # Let Flask start serving first
        while True:
            try:
                results, timing = loop.run_until_complete(
                    okx_client.fetch_and_calculate_all(state_ref, state_lock, build_genome())
                )
                with state_lock:
                    state_ref["indicator_cache"] = results
                    state_ref["data_status"] = {
                        pair: (r.get("data_points", 0), r.get("signal", "?"), r.get("error", ""))
                        for pair, r in results.items()
                    }
                    state_ref["last_error"] = None
                    state_ref["last_timing"] = timing
                    state_ref["last_update"] = arrow.now().format("YYYY-MM-DD HH:mm:ss")
                mark_dirty()
                logger.info("[DATA_UPDATER] updated %d pairs, fetch=%sms calc=%sms",
                           len(results), timing.get('fetch_ms'), timing.get('calc_ms'))
            except Exception as e:
                logger.error("[DATA_UPDATER] ERROR: %s", e)
                traceback.print_exc()
                with state_lock:
                    state_ref["last_error"] = f"Data update failed: {e}"
            time.sleep(7200)  # 2h interval for 4h candles + LightGBM

    t = threading.Thread(target=data_updater, daemon=True, name="data-updater")
    t.start()
    return t


def start_price_updater(state_ref: Dict[str, Any], state_lock: threading.Lock):
    import exchange.client as okx_client

    def price_updater():
        loop = _run_async_loop()
        while True:
            try:
                logger.info("[PRICE_UPDATER] starting fetch...")
                fetch_start = time.time()
                prices = loop.run_until_complete(okx_client.fetch_all_prices(state_ref, state_lock))
                fetch_ms = round((time.time() - fetch_start) * 1000)
                with state_lock:
                    state_ref["price_cache"].update(prices)
                    state_ref["last_error"] = None
                    state_ref["price_updated"] = arrow.now().format("YYYY-MM-DD HH:mm:ss")
                    state_ref["price_timing"] = {"fetch_ms": fetch_ms, "total_ms": fetch_ms}
                logger.info("[PRICE_UPDATER] price_cache now has %d entries",
                           len(state_ref['price_cache']))
            except Exception as e:
                logger.error("[PRICE_UPDATER] ERROR: %s", e)
                traceback.print_exc()
                with state_lock:
                    state_ref["last_error"] = f"Price update failed: {e}"
            time.sleep(30)

    t = threading.Thread(target=price_updater, daemon=True, name="price-updater")
    t.start()
    return t


def start_state_persistor(state_ref: Dict[str, Any], state_lock: threading.Lock, save_state_func):
    from core.state import is_dirty

    def state_persistor():
        while True:
            try:
                with state_lock:
                    if is_dirty():
                        save_state_func()
                        logger.debug("[STATE_PERSISTOR] state saved")
            except Exception as e:
                logger.error("[STATE_PERSISTOR] ERROR: %s", e)
            time.sleep(60)

    t = threading.Thread(target=state_persistor, daemon=True, name="state-persistor")
    t.start()
    return t


def _v(val, default=None):
    from indicators.calculator import safe_val
    return safe_val(val, default)


def start_strategy_tick(state_ref, state_lock, genome, notify_signal_func, save_state_func):
    import indicators.calculator as indicators
    from core.constants import build_genome
    from core.state import mark_dirty
    CIRCUIT_BREAK_LOSSES = 5
    CIRCUIT_BREAK_DURATION = 900
    MAX_OPEN_PER_TICK = 999999  # No per-tick limit — controlled via pair cooldown
    MAX_DATA_AGE_SECONDS = 86400  # 24h — allow trading on cached data
    PAIR_COOLDOWN_SECONDS = 1800  # 30 min cooldown after closing before re-opening same pair

    def _get_time_based_tp_hours():
        """Time-based TP: close profitable positions after N hours."""
        from strategy_manager import get_general_params
        max_bars = get_general_params().get("max_bars", 50)
        hours = max_bars * 15 / 60  # bars → hours
        return min(hours, 12.0)  # cap at 12 hours

    def _get_fee():
        from strategy_manager import get_general_params
        return get_general_params().get("fee", 0.0015)

    def _get_max_positions():
        mode = state_ref.get("mode", "safe")
        if mode == "safe":
            return 999999
        return 999999  # No position cap — overtrading controlled via pair cooldown

    def strategy_tick():
        while True:
            try:
                if not state_ref.get("strategy_active", False):
                    logger.info("[STRATEGY] not active, sleeping 30s")
                    time.sleep(30)
                    continue

                now = arrow.now().format("YYYY-MM-DD HH:mm:ss")
                signals = []
                fee = _get_fee()
                max_positions = _get_max_positions()
                _current_genome = build_genome()
                _leverage = _current_genome.get("leverage",
                    _current_genome.get("general", {}).get("leverage", 3.0))
                _fee = _current_genome.get("fee",
                    _current_genome.get("general", {}).get("fee", 0.0015))

                # Phase 1: Acquire lock, read ALL state needed for this tick
                with state_lock:
                    check_circuit = state_ref.get("circuit_broken", False)
                    cb_until = state_ref.get("circuit_broken_until")
                    if check_circuit and cb_until:
                        if time.time() < cb_until:
                            signals.append(f"[{now}] CIRCUIT BREAKER ACTIVE - no new trades until {arrow.Arrow.fromtimestamp(cb_until).to('local').format('YYYY-MM-DD HH:mm:ss')}")
                        else:
                            state_ref["circuit_broken"] = False
                            state_ref["circuit_broken_until"] = None
                            state_ref["consecutive_losses"] = 0
                            signals.append(f"[{now}] CIRCUIT BREAKER CLEARED - resuming")
                    else:
                        state_ref["circuit_broken"] = False

                    open_positions = list(state_ref.get("open_positions", []))
                    open_count = len(open_positions)
                    indicator_cache = dict(state_ref.get("indicator_cache", {}))
                    price_cache = dict(state_ref.get("price_cache", {}))
                    position_history = list(state_ref.get("position_history", []))
                    consecutive_losses = state_ref.get("consecutive_losses", 0)
                    circuit_broken = state_ref.get("circuit_broken", False)
                    pair_last_closed = state_ref.get("pair_last_closed", {})

                # Phase 2: Process signals OUTSIDE the lock (CPU work, no state mutation)
                if not indicator_cache:
                    signals.append(f"[{now}] No indicator data ready yet")
                else:
                    positions_to_close = []
                    positions_to_open = []

                    opened_this_tick = 0

                    for pair, data in indicator_cache.items():
                        try:
                            sig = data.get("signal", "HOLD")

                            kline_ts = data.get("ts") or data.get("kline_ts")
                            if kline_ts:
                                age_seconds = (time.time() * 1000 - kline_ts) / 1000
                                if age_seconds > MAX_DATA_AGE_SECONDS:
                                    signals.append(f"[{now}] {pair} -> {sig} SKIPPED (data age={age_seconds:.0f}s > {MAX_DATA_AGE_SECONDS}s)")
                                    continue

                            stale_close = _v(data.get("close"), 0)
                            current_price_str = price_cache.get(pair, {}).get("last")
                            try:
                                current_price = float(current_price_str) if current_price_str else stale_close
                            except (ValueError, TypeError):
                                current_price = stale_close
                            if not current_price or current_price == 0:
                                continue
                            close = current_price
                            atr = _v(data.get("atr_14"), 1)

                            pos = next((p for p in open_positions if p["pair"] == pair), None)

                            if pos:
                                pos_age_hours = 0
                                if "sl" not in pos or "tp" not in pos:
                                    signals.append(f"[{now}] {pair} WARNING: position missing sl/tp keys, skipping TP/SL check")
                                else:
                                    hit = False
                                    exit_reason = ""
                                    if pos["direction"] == "LONG":
                                        if close <= pos["sl"] or close >= pos["tp"]:
                                            hit = True
                                            exit_reason = "SL" if close <= pos["sl"] else "TP"
                                    else:
                                        if close >= pos["sl"] or close <= pos["tp"]:
                                            hit = True
                                            exit_reason = "SL" if close >= pos["sl"] else "TP"

                                    if not hit:
                                        pnl = (close / pos["entry"] - 1) * _leverage
                                        if pos["direction"] == "SHORT":
                                            pnl = -pnl
                                        try:
                                            opened_str = pos.get("opened_at", "")
                                            if opened_str:
                                                opened_arrow = arrow.get(opened_str)
                                                local_tz = arrow.now().tzinfo
                                                opened_local = opened_arrow.replace(tzinfo=local_tz)
                                                pos_age_hours = (time.time() - opened_local.timestamp()) / 3600
                                            else:
                                                pos_age_hours = 0
                                        except Exception:
                                            pos_age_hours = 0
                                        if pos_age_hours >= _get_time_based_tp_hours() and pnl > 0:
                                            hit = True
                                            exit_reason = "TIME_TP"
                                            pnl -= _fee * 2

                                    if hit:
                                        pnl = (close / pos["entry"] - 1) * _leverage
                                        if pos["direction"] == "SHORT":
                                            pnl = -pnl
                                        pnl -= fee * 2
                                        win = pnl > 0
                                        positions_to_close.append({
                                            "pair": pair, "direction": pos["direction"],
                                            "entry": pos["entry"], "exit": close,
                                            "exit_reason": exit_reason, "pnl": round(pnl * 100, 2),
                                            "win": win, "closed_at": now,
                                            "notify_args": (pair, pos["direction"], close, exit_reason, pnl * 100),
                                        })
                                        if not win:
                                            consecutive_losses += 1
                                            if consecutive_losses >= CIRCUIT_BREAK_LOSSES:
                                                circuit_broken = True
                                                signals.append(f"[{now}] {pair} LOSS - 5 consecutive! CIRCUIT BREAKER TRIGGERED for 15min")
                                            else:
                                                signals.append(f"[{now}] {pair} CLOSE {exit_reason} PnL={pnl*100:.2f}% [{pos['direction']}] | Losses:{consecutive_losses}/5")
                                        else:
                                            consecutive_losses = 0
                                            signals.append(f"[{now}] {pair} CLOSE {exit_reason} PnL={pnl*100:.2f}% [{pos['direction']}] | WIN")
                                        open_count -= 1
                                    else:
                                        signals.append(f"[{now}] {pair} {close:.10f} {pos['direction']} held SL={pos['sl']:.10f} TP={pos['tp']:.10f} age={pos_age_hours:.1f}h")

                            elif sig == "HOLD":
                                pass

                            elif sig != "HOLD" and not circuit_broken and open_count < max_positions and opened_this_tick < MAX_OPEN_PER_TICK and not any(p["pair"] == pair for p in open_positions) and (time.time() - pair_last_closed.get(pair, 0)) >= PAIR_COOLDOWN_SECONDS:
                                direction = sig
                                entry_price = close
                                if "regimes" in _current_genome:
                                    from indicators.calculator import _detect_trend
                                    regime_mode = _current_genome.get("regime_mode", "auto")
                                    if regime_mode in ("bull", "bear", "range"):
                                        regime = regime_mode
                                    else:
                                        ema20 = indicators._safe_get(data, "ema_25", 0)
                                        ema50 = indicators._safe_get(data, "ema_50", 0)
                                        regime = _detect_trend(ema20, ema50, {"trend": {"slope_threshold": 0.02}})
                                    rp = _current_genome["regimes"].get(regime, _current_genome["regimes"]["bull"])
                                    atr_sl_mult = rp.get("atr_sl", 3.65)
                                    atr_tp_mult = rp.get("atr_tp", 5.51)
                                else:
                                    atr_sl_mult = _current_genome.get("atr_mult_sl", 3.99)
                                    atr_tp_mult = _current_genome.get("atr_mult_tp", 3.61)
                                sl_price = entry_price - atr * atr_sl_mult if direction == "LONG" else entry_price + atr * atr_sl_mult
                                tp_price = entry_price + atr * atr_tp_mult if direction == "LONG" else entry_price - atr * atr_tp_mult
                                positions_to_open.append({
                                    "pair": pair, "direction": direction,
                                    "entry": entry_price, "sl": sl_price, "tp": tp_price,
                                    "opened_at": now, "entry_bar": data.get("ts"),
                                    "notify_args": (pair, direction, entry_price, "OPEN", None),
                                })
                                open_count += 1
                                opened_this_tick += 1
                                signals.append(f"[{now}] {pair} OPEN {direction} @ {entry_price:.10f} SL={sl_price:.10f} TP={tp_price:.10f} | Pos:{open_count}/{max_positions if max_positions < 999999 else '∞'}")
                            elif sig != "HOLD":
                                _blk = []
                                if circuit_broken: _blk.append("circuit")
                                if open_count >= max_positions: _blk.append(f"full({open_count}/{max_positions if max_positions < 999999 else '∞'})")
                                if opened_this_tick >= MAX_OPEN_PER_TICK: _blk.append(f"per_tick_limit({MAX_OPEN_PER_TICK})")
                                if any(p["pair"] == pair for p in open_positions): _blk.append("in_pos")
                                cd_remaining = PAIR_COOLDOWN_SECONDS - (time.time() - pair_last_closed.get(pair, 0))
                                if cd_remaining > 0: _blk.append(f"cooldown({cd_remaining:.0f}s)")
                                _blk_str = ",".join(_blk) if _blk else "unknown"
                                logger.warning(f"[STRATEGY] {pair} {close:.10f} -> {sig} (BLOCKED by {_blk_str})")

                        except Exception as pair_err:
                            logger.error(f"[STRATEGY] ERROR processing {pair}: {pair_err}")
                            traceback.print_exc()

                    with state_lock:
                        for close_info in positions_to_close:
                            state_ref["position_history"].append({
                                "pair": close_info["pair"], "direction": close_info["direction"],
                                "entry": close_info["entry"], "exit": close_info["exit"],
                                "exit_reason": close_info["exit_reason"], "pnl": close_info["pnl"],
                                "win": close_info["win"], "closed_at": close_info["closed_at"],
                            })
                            state_ref["open_positions"] = [p for p in state_ref["open_positions"] if p["pair"] != close_info["pair"]]
                            state_ref["consecutive_losses"] = consecutive_losses
                            state_ref.setdefault("pair_last_closed", {})[close_info["pair"]] = time.time()
                            if circuit_broken:
                                state_ref["circuit_broken"] = True
                                state_ref["circuit_broken_until"] = time.time() + CIRCUIT_BREAK_DURATION
                        state_ref["position_history"] = state_ref["position_history"][-200:]
                        for open_info in positions_to_open:
                            state_ref["open_positions"].append({k: v for k, v in open_info.items() if k != "notify_args"})
                        open_positions = list(state_ref["open_positions"])

                    for close_info in positions_to_close:
                        notify_signal_func(state_ref, *close_info["notify_args"])
                    for open_info in positions_to_open:
                        notify_signal_func(state_ref, *open_info["notify_args"])

                    if positions_to_close or positions_to_open:
                        mark_dirty()

                if not signals:
                    signals = [f"[{now}] No signals"]

                wins = sum(1 for h in position_history if h.get("win", False))
                total_closed = len(position_history)
                entry = {
                    "time": now,
                    "signals": signals,
                    "open_pos": len(open_positions),
                    "consecutive_losses": consecutive_losses,
                    "circuit_broken": circuit_broken,
                    "win_rate": round(wins / total_closed * 100, 1) if total_closed > 0 else 0,
                }
                with state_lock:
                    state_ref["strategy_log"].insert(0, entry)
                    state_ref["strategy_log"] = state_ref["strategy_log"][:100]

                logger.info(f"[STRATEGY] {now} tick done: {len(signals)} signals, {len(open_positions)} open positions, {len(indicator_cache)} pairs")

            except Exception as e:
                logger.error(f"[STRATEGY] CRITICAL ERROR: {e}")
                traceback.print_exc()

            time.sleep(30)

    threading.Thread(target=strategy_tick, daemon=True).start()


def launch_all_threads(state_ref, state_lock, genome, save_state_func, notify_signal_func):
    start_data_updater(state_ref, state_lock, genome, save_state_func)
    start_price_updater(state_ref, state_lock)
    start_strategy_tick(state_ref, state_lock, genome, notify_signal_func, save_state_func)
    start_state_persistor(state_ref, state_lock, save_state_func)
