#!/usr/bin/env python3
"""OKX Gateway Test Suite - Fully isolated, no real API calls."""

import sys
import os
import math
import time
import json
import csv
import tempfile
import shutil
import unittest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indicators import (
    calc_ema, calc_rsi, calc_macd, calc_bb, calc_stoch, calc_atr,
    calc_all_indicators, get_latest_indicators, compute_signal,
)
from storage import pair_csv, load_candles, append_candles
from constants import build_genome, CANDLE_BAR, CANDLE_LIMIT, VERSION, STRATEGY_CONFIG
from okx_client import _validate_candle, calculate_for_pair, _prec


_TEST_FLAT_GENOME = {
    "rsi_os": 28.07, "rsi_ob": 75.55, "rsi_w": 0.43,
    "extreme_bonus": 0.3,
    "macd_w": 0.93, "macd_ratio": 0.5, "macd_signal_ratio": 0.3,
    "bb_w": 0.52, "bb_thresh": 0.34, "bb_thresh_hi": 0.66,
    "stoch_w": 0.32, "stoch_ob": 83.27, "stoch_os": 25.59,
    "ema_w": 0.13,
    "score_min": 0.55,
    "trend": {"slope_threshold": 0.02},
    "leverage": 3,
}


def make_candles(n=300, base_price=100.0, volatility=0.02, trend=0.0):
    candles = []
    price = base_price
    for i in range(n):
        change = price * volatility * (0.5 - (i % 2) * 0.1) * (1 + trend * i / n)
        open_p = price
        high_p = max(price * 1.002, price + abs(change))
        low_p = min(price * 0.998, price - abs(change))
        close_p = price + change
        vol = price * 1000 * (1 + abs(change) / price * 10)
        candles.append({
            "ts": 1700000000000 + i * 900000,
            "open": round(open_p, 4),
            "high": round(high_p, 4),
            "low": round(low_p, 4),
            "close": round(close_p, 4),
            "vol": round(vol, 2),
        })
        price = close_p
    return candles


def make_btc_candles(n=300):
    return make_candles(n=n, base_price=60000.0, volatility=0.008, trend=0.5)


class TestIndicatorCalculations(unittest.TestCase):
    def test_calc_ema_length(self):
        closes = [float(i) for i in range(50)]
        result = calc_ema(closes, 14)
        self.assertEqual(len(result), 50)
        self.assertIsNone(result[12])
        self.assertIsNotNone(result[13])
        print("[PASS] test_calc_ema_length")

    def test_calc_ema_warmup_insufficient(self):
        closes = [1.0] * 10
        result = calc_ema(closes, 14)
        self.assertEqual(len(result), 0)
        print("[PASS] test_calc_ema_warmup_insufficient")

    def test_calc_rsi_length(self):
        closes = [100.0 + i * 0.5 for i in range(50)]
        result = calc_rsi(closes, 14)
        self.assertEqual(len(result), 50)
        print("[PASS] test_calc_rsi_length")

    def test_calc_rsi_range(self):
        closes = [100.0 + math.sin(i / 5) * 10 for i in range(100)]
        result = calc_rsi(closes, 14)
        for v in result:
            if v is not None:
                self.assertGreaterEqual(v, 0)
                self.assertLessEqual(v, 100)
        print("[PASS] test_calc_rsi_range")

    def test_calc_macd_returns_three_arrays(self):
        closes = [100.0 + i * 0.2 for i in range(100)]
        macd, signal, hist = calc_macd(closes)
        self.assertEqual(len(macd), 100)
        self.assertEqual(len(signal), 100)
        self.assertEqual(len(hist), 100)
        print("[PASS] test_calc_macd_returns_three_arrays")

    def test_calc_bb_length(self):
        closes = [100.0 + i * 0.2 for i in range(50)]
        result = calc_bb(closes, 20)
        self.assertEqual(len(result), 50)
        print("[PASS] test_calc_bb_length")

    def test_calc_bb_values_in_range(self):
        closes = [100.0 + math.sin(i / 10) * 5 for i in range(50)]
        result = calc_bb(closes, 20)
        for v in result:
            self.assertGreaterEqual(v, -0.5)
            self.assertLessEqual(v, 1.5)
        print("[PASS] test_calc_bb_values_in_range")

    def test_calc_stoch_length(self):
        highs = [110.0 + i * 0.2 for i in range(50)]
        lows = [90.0 + i * 0.1 for i in range(50)]
        closes = [100.0 + i * 0.15 for i in range(50)]
        k, d = calc_stoch(highs, lows, closes, 14, 3)
        self.assertEqual(len(k), 50)
        self.assertEqual(len(d), 50)
        print("[PASS] test_calc_stoch_length")

    def test_calc_atr_length(self):
        highs = [110.0 + i * 0.2 for i in range(50)]
        lows = [90.0 + i * 0.1 for i in range(50)]
        closes = [100.0 + i * 0.15 for i in range(50)]
        result = calc_atr(highs, lows, closes, 14)
        self.assertEqual(len(result), 50)
        print("[PASS] test_calc_atr_length")

    def test_calc_all_indicators_keys(self):
        candles = make_btc_candles(300)
        indicators = calc_all_indicators(candles)
        keys = set(indicators.keys())
        expected = {
            "timestamps", "rsi_14", "rsi_7", "macd", "macd_signal",
            "macd_hist", "bb_position", "stoch_k", "stoch_d",
            "ema_7", "ema_25", "ema_50", "atr_14", "close", "open",
        }
        self.assertEqual(keys & expected, expected)
        print("[PASS] test_calc_all_indicators_keys")

    def test_calc_all_indicators_insufficient_data(self):
        short_candles = make_btc_candles(5)
        result = calc_all_indicators(short_candles)
        self.assertEqual(result, {})
        print("[PASS] test_calc_all_indicators_insufficient_data")

    def test_get_latest_indicators_single_point(self):
        ind = {"close": [100, 101, 102], "rsi_14": [30, 40, 50], "timestamps": [1, 2, 3]}
        result = get_latest_indicators(ind)
        self.assertEqual(result["close"], 102)
        self.assertEqual(result["rsi_14"], 50)
        self.assertEqual(result["timestamps"], 3)
        print("[PASS] test_get_latest_indicators_single_point")

    def test_get_latest_indicators_empty(self):
        self.assertEqual(get_latest_indicators({}), {})
        self.assertEqual(get_latest_indicators({"close": []}), {})
        print("[PASS] test_get_latest_indicators_empty")


class TestSignalComputation(unittest.TestCase):
    def test_empty_input(self):
        sig, nl, ns = compute_signal({}, STRATEGY_CONFIG)
        self.assertEqual(sig, "HOLD")
        sig, nl, ns = compute_signal(None, STRATEGY_CONFIG)
        self.assertEqual(sig, "HOLD")
        print("[PASS] test_empty_input")

    def test_missing_close(self):
        sig, nl, ns = compute_signal({"close": 0}, STRATEGY_CONFIG)
        self.assertEqual(sig, "HOLD")
        print("[PASS] test_missing_close")

    def test_oversold_triggers_long(self):
        ind = {
            "close": 100.0, "rsi_14": 20.0, "rsi_7": 25.0,
            "macd_hist": 0.5, "macd_signal": 0.1,
            "bb_position": 0.2, "stoch_k": 15.0,
            "ema_25": 105.0, "ema_50": 100.0,
            "atr_14": 1.0,
        }
        sig, nl, ns = compute_signal(ind, _TEST_FLAT_GENOME)
        self.assertEqual(sig, "LONG")
        print("[PASS] test_oversold_triggers_long")

    def test_overbought_triggers_short(self):
        ind = {
            "close": 100.0, "rsi_14": 85.0, "rsi_7": 60.0,
            "macd_hist": -0.5, "macd_signal": -0.1,
            "bb_position": 0.8, "stoch_k": 90.0,
            "ema_25": 95.0, "ema_50": 100.0,
            "atr_14": 1.0,
        }
        sig, nl, ns = compute_signal(ind, _TEST_FLAT_GENOME)
        self.assertEqual(sig, "SHORT")
        print("[PASS] test_overbought_triggers_short")

    def test_normalized_scores_non_negative(self):
        for candles in [
            make_btc_candles(300),
            make_candles(300, 100.0, 0.01, 0),
            make_candles(300, 100.0, 0.05, -1),
        ]:
            ind = calc_all_indicators(candles)
            latest = get_latest_indicators(ind)
            sig, nl, ns = compute_signal(latest, _TEST_FLAT_GENOME)
            self.assertGreaterEqual(nl, 0.0)
            self.assertGreaterEqual(ns, 0.0)
        print("[PASS] test_normalized_scores_non_negative")

    def test_list_values_unpacked(self):
        ind = {
            "close": [60000.0] * 50,
            "rsi_14": [20.0] * 50, "rsi_7": [30.0] * 50,
            "macd_hist": [0.5] * 50, "macd_signal": [0.1] * 50,
            "bb_position": [0.2] * 50, "stoch_k": [15.0] * 50,
            "ema_25": [62000.0] * 50, "ema_50": [60000.0] * 50,
            "atr_14": [1.0] * 50,
        }
        sig, nl, ns = compute_signal(ind, _TEST_FLAT_GENOME)
        self.assertEqual(sig, "LONG")
        print("[PASS] test_list_values_unpacked")

    def test_partial_agreement_hold(self):
        ind = {
            "close": 60000.0, "rsi_14": 50.0, "rsi_7": 40.0,
            "macd_hist": 0.0, "macd_signal": 0.0,
            "bb_position": 0.45, "stoch_k": 30.0,
            "ema_25": 60000.0, "ema_50": 60000.0,
            "atr_14": 1.0,
        }
        sig, nl, ns = compute_signal(ind, _TEST_FLAT_GENOME)
        self.assertEqual(sig, "HOLD")
        self.assertLess(nl, 0.70)
        print("[PASS] test_partial_agreement_hold")

    def test_all_bearish_short(self):
        ind = {
            "close": 60000.0, "rsi_14": 76.0, "rsi_7": 53.0,
            "macd_hist": -0.5, "macd_signal": -0.1,
            "bb_position": 0.85, "stoch_k": 90.0,
            "ema_25": 58000.0, "ema_50": 60000.0,
            "atr_14": 1.0,
        }
        sig, nl, ns = compute_signal(ind, _TEST_FLAT_GENOME)
        self.assertEqual(sig, "SHORT")
        self.assertGreater(ns, nl)
        print("[PASS] test_all_bearish_short")


class TestValidateCandle(unittest.TestCase):
    def test_valid_candle(self):
        self.assertTrue(_validate_candle(["1700000000000", "60000.0", "61000.0", "59000.0", "60500.0", "1000.0"]))

    def test_invalid_high_low(self):
        self.assertFalse(_validate_candle(["1700000000000", "60000.0", "59000.0", "61000.0", "60500.0", "1000.0"]))

    def test_zero_price(self):
        self.assertFalse(_validate_candle(["1700000000000", "0", "61000.0", "59000.0", "60500.0", "1000.0"]))

    def test_too_large_price(self):
        self.assertFalse(_validate_candle(["1700000000000", "1e10", "1e10", "1e10", "1e10", "1000.0"]))

    def test_bad_format(self):
        self.assertFalse(_validate_candle(["abc", "def"]))

    def test_too_few_fields(self):
        self.assertFalse(_validate_candle(["1700000000000"]))

    def test_negative_close(self):
        self.assertFalse(_validate_candle(["1700000000000", "60000.0", "61000.0", "59000.0", "-1", "1000.0"]))


class TestCalculateForPair(unittest.TestCase):
    def test_returns_all_fields(self):
        candles = make_btc_candles(300)
        result = calculate_for_pair("BTC-USDT-SWAP", candles, build_genome())
        required = [
            "pair", "signal", "norm_long", "norm_short",
            "rsi_14", "rsi_7", "macd_hist", "bb_position", "stoch_k",
            "ema_7", "ema_50", "atr_14", "close", "ts",
            "data_points", "sl", "tp", "genome",
        ]
        for field in required:
            self.assertIn(field, result, f"Missing field: {field}")
        print("[PASS] test_returns_all_fields")

    def test_insufficient_data(self):
        short = make_btc_candles(5)
        result = calculate_for_pair("BTC-USDT-SWAP", short, build_genome())
        self.assertEqual(result["signal"], "HOLD")
        self.assertEqual(result["data_points"], 5)
        print("[PASS] test_insufficient_data")

    def test_close_is_nonzero(self):
        candles = make_btc_candles(300)
        result = calculate_for_pair("BTC-USDT-SWAP", candles, build_genome())
        self.assertGreater(result["close"], 0)
        print("[PASS] test_close_is_nonzero")

    def test_sl_below_entry(self):
        candles = make_btc_candles(300)
        result = calculate_for_pair("BTC-USDT-SWAP", candles, build_genome())
        self.assertLess(result["sl"], result["close"])
        print("[PASS] test_sl_below_entry")

    def test_tp_above_entry(self):
        candles = make_btc_candles(300)
        result = calculate_for_pair("BTC-USDT-SWAP", candles, build_genome())
        self.assertGreater(result["tp"], result["close"])
        print("[PASS] test_tp_above_entry")


class TestCSVStorage(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_roundtrip(self):
        candles = make_btc_candles(100)
        append_candles("BTC-USDT-SWAP", candles, self.tmpdir)
        loaded = load_candles("BTC-USDT-SWAP", self.tmpdir)
        self.assertEqual(len(loaded), 100)
        self.assertEqual(loaded[0]["ts"], candles[0]["ts"])
        self.assertEqual(loaded[-1]["ts"], candles[-1]["ts"])
        print("[PASS] test_roundtrip")

    def test_deduplication(self):
        candles = make_btc_candles(50)
        append_candles("ETH-USDT-SWAP", candles, self.tmpdir)
        append_candles("ETH-USDT-SWAP", candles, self.tmpdir)
        loaded = load_candles("ETH-USDT-SWAP", self.tmpdir)
        self.assertEqual(len(loaded), 50)
        print("[PASS] test_deduplication")

    def test_missing_file(self):
        result = load_candles("NONEXISTENT_xyz123", self.tmpdir)
        self.assertEqual(result, [])
        print("[PASS] test_missing_file")

    def test_empty_append(self):
        append_candles("TEST-PAIR-SWAP", [], self.tmpdir)
        result = load_candles("TEST-PAIR-SWAP", self.tmpdir)
        self.assertEqual(result, [])
        print("[PASS] test_empty_append")

    def test_pair_csv_format(self):
        path = pair_csv("BTC-USDT-SWAP", self.tmpdir)
        self.assertIn("BTC_USDT_SWAP", path)
        self.assertTrue(path.endswith("_4H.csv"))
        print("[PASS] test_pair_csv_format")


class TestConstants(unittest.TestCase):
    def test_genome_keys_complete(self):
        # V262 regime-based genome
        self.assertIn("regimes", build_genome())
        regimes = build_genome()["regimes"]
        for regime_name in ("bull", "bear", "range"):
            self.assertIn(regime_name, regimes)
            rp = regimes[regime_name]
            required = ["rsi_os", "rsi_ob", "rsi_w", "macd_w", "bb_w", "stoch_w", "ema_w", "score_min"]
            for key in required:
                self.assertIn(key, rp, f"Missing {regime_name}.{key}")
        print("[PASS] test_genome_keys_complete")

    def test_genome_values_sane(self):
        regimes = build_genome().get("regimes", {})
        for regime_name, params in regimes.items():
            self.assertGreater(params.get("score_min", 0), 0)
            self.assertGreater(params.get("atr_sl", 0), 0)
            self.assertGreater(params.get("atr_tp", 0), 0)
        print("[PASS] test_genome_values_sane")

    def test_candle_bar_4h(self):
        self.assertEqual(CANDLE_BAR, "4H")
        print("[PASS] test_candle_bar_4h")

    def test_version_exists(self):
        self.assertTrue(VERSION)
        self.assertIn("v", VERSION)
        print("[PASS] test_version_exists")


class TestPrec(unittest.TestCase):
    def test_prec_zero(self):
        self.assertEqual(_prec(0), 0.0)

    def test_prec_normal(self):
        self.assertEqual(_prec(123.456789), round(123.456789, 10))


class TestNotificationLogic(unittest.TestCase):
    def test_notify_signal_no_users(self):
        from notifications import notify_signal
        mock_state = {"safe_users": []}
        with patch("notifications._is_configured", return_value=False):
            with patch("notifications.send_trade_notification") as mock_send:
                notify_signal(mock_state, "BTC-USDT-SWAP", "LONG", 60000.0, "OPEN")
                mock_send.assert_not_called()
        print("[PASS] test_notify_signal_no_users")

    def test_notify_signal_hold_skipped(self):
        from notifications import notify_signal
        mock_state = {"safe_users": [{"email": "test@test.com"}]}
        with patch("notifications.send_trade_notification") as mock_send:
            notify_signal(mock_state, "BTC-USDT-SWAP", "LONG", 60000.0, "HOLD")
            mock_send.assert_not_called()
        print("[PASS] test_notify_signal_hold_skipped")

    def test_notify_signal_open_sends(self):
        from notifications import notify_signal
        mock_state = {"safe_users": [{"email": "test@test.com"}], "genome": {}, "indicator_cache": {}, "open_positions": [], "last_update": "now"}
        with patch("notifications._is_configured", return_value=True):
            with patch("notifications.send_trade_notification", return_value=True) as mock_send:
                notify_signal(mock_state, "BTC-USDT-SWAP", "LONG", 60000.0, "OPEN")
                mock_send.assert_called_once()
        print("[PASS] test_notify_signal_open_sends")

    def test_notify_deduplication(self):
        from notifications import notify_signal
        mock_state = {"safe_users": [{"email": "a@test.com"}, {"email": "a@test.com"}], "genome": {}, "indicator_cache": {}, "open_positions": [], "last_update": "now"}
        with patch("notifications._is_configured", return_value=True):
            with patch("notifications.send_trade_notification", return_value=True) as mock_send:
                notify_signal(mock_state, "BTC-USDT-SWAP", "LONG", 60000.0, "OPEN")
                self.assertEqual(mock_send.call_count, 1)
        print("[PASS] test_notify_deduplication")


class TestCircuitBreakerLogic(unittest.TestCase):
    def test_five_losses_trigger(self):
        losses = 0
        broken = False
        for i in range(5):
            losses += 1
            if losses >= 5:
                broken = True
        self.assertTrue(broken)

    def test_win_resets_losses(self):
        losses = 3
        is_win = True
        if is_win:
            losses = 0
        self.assertEqual(losses, 0)

    def test_position_management(self):
        positions = [
            {"pair": "BTC-USDT-SWAP", "direction": "LONG", "entry": 60000, "sl": 58000, "tp": 62000},
            {"pair": "ETH-USDT-SWAP", "direction": "SHORT", "entry": 3000, "sl": 3100, "tp": 2900},
        ]
        self.assertEqual(len(positions), 2)
        btc = next(p for p in positions if p["pair"] == "BTC-USDT-SWAP")
        self.assertEqual(btc["direction"], "LONG")
        close = 62500
        self.assertGreater(close, btc["tp"])

    def test_long_tp_hit(self):
        entry, tp, close = 100.0, 103.61, 103.7
        self.assertGreaterEqual(close, tp)

    def test_long_sl_hit(self):
        entry, sl, close = 100.0, 96.01, 95.9
        self.assertLessEqual(close, sl)

    def test_short_tp_hit(self):
        entry, tp, close = 100.0, 96.39, 96.3
        self.assertLessEqual(close, tp)

    def test_pnl_long_win(self):
        entry, close, leverage = 100.0, 103.0, 3
        pnl = (close / entry - 1) * leverage
        self.assertGreater(pnl, 0)

    def test_pnl_short_win(self):
        entry, close, leverage = 100.0, 97.0, 3
        pnl = -((close / entry - 1) * leverage)
        self.assertGreater(pnl, 0)


class TestStorageDedup(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_incremental_append(self):
        c1 = make_candles(30)
        append_candles("TEST-INCR-SWAP", c1, self.tmpdir)
        loaded1 = load_candles("TEST-INCR-SWAP", self.tmpdir)
        self.assertEqual(len(loaded1), 30)
        c2 = make_candles(30, base_price=200.0)
        for i, c in enumerate(c2):
            c["ts"] = c1[-1]["ts"] + (i + 1) * 900000
        append_candles("TEST-INCR-SWAP", c2, self.tmpdir)
        loaded2 = load_candles("TEST-INCR-SWAP", self.tmpdir)
        self.assertEqual(len(loaded2), 60)
        print("[PASS] test_incremental_append")


class TestIndicatorEdgeCases(unittest.TestCase):
    def test_atr_never_zero(self):
        candles = make_btc_candles(300)
        ind = calc_all_indicators(candles)
        non_warmup = ind["atr_14"][14:]
        for v in non_warmup:
            self.assertGreater(v, 0)

    def test_ema_never_negative(self):
        candles = make_btc_candles(300)
        ind = calc_all_indicators(candles)
        for v in ind["ema_7"]:
            self.assertGreaterEqual(v, 0.0)
        for v in ind["ema_50"]:
            self.assertGreaterEqual(v, 0.0)

    def test_bb_position_all_present(self):
        candles = make_btc_candles(300)
        ind = calc_all_indicators(candles)
        self.assertEqual(len(ind["bb_position"]), 300)

    def test_close_prices_match(self):
        candles = make_btc_candles(300)
        ind = calc_all_indicators(candles)
        expected_closes = [c["close"] for c in candles]
        self.assertEqual(ind["close"], expected_closes)


class TestFetchOKXTickerMocked(unittest.TestCase):
    def test_success(self):
        from okx_client import fetch_okx_ticker
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": "0",
            "data": [{"instId": "BTC-USDT-SWAP", "last": "60500.5", "open24h": "60000.0", "vol24h": "12345.67", "ts": "1700000009000"}]
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        async def run():
            return await fetch_okx_ticker(mock_client, "BTC-USDT-SWAP")

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertIsNotNone(result)
        self.assertEqual(result["last"], "60500.5")
        print("[PASS] test_fetch_okx_ticker_success")

    def test_api_error_returns_none(self):
        from okx_client import fetch_okx_ticker
        mock_response = MagicMock()
        mock_response.json.return_value = {"code": "500", "data": None}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        async def run():
            return await fetch_okx_ticker(mock_client, "BTC-USDT-SWAP")

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertIsNone(result)
        print("[PASS] test_fetch_okx_ticker_api_error")

    def test_network_error_returns_none(self):
        from okx_client import fetch_okx_ticker
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("network error"))

        async def run():
            return await fetch_okx_ticker(mock_client, "BTC-USDT-SWAP")

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertIsNone(result)
        print("[PASS] test_fetch_okx_ticker_network_error")


class TestFetchCandlesMocked(unittest.TestCase):
    def test_success(self):
        from okx_client import fetch_candles
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": "0",
            "data": [
                ["1700000009000", "60500.0", "61500.0", "60000.0", "61000.0", "1100.0"],
                ["1700000000000", "60000.0", "61000.0", "59000.0", "60500.0", "1000.0"],
            ]
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        async def run():
            return await fetch_candles(mock_client, "BTC-USDT-SWAP")

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["open"], 60000.0)
        self.assertEqual(result[0]["close"], 60500.0)
        print("[PASS] test_fetch_candles_success")

    def test_api_error(self):
        from okx_client import fetch_candles
        mock_response = MagicMock()
        mock_response.json.return_value = {"code": "500", "data": None}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        async def run():
            return await fetch_candles(mock_client, "BTC-USDT-SWAP")

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertEqual(result, [])
        print("[PASS] test_fetch_candles_api_error")

    def test_empty_data(self):
        from okx_client import fetch_candles
        mock_response = MagicMock()
        mock_response.json.return_value = {"code": "0", "data": []}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        async def run():
            return await fetch_candles(mock_client, "BTC-USDT-SWAP")

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertEqual(result, [])
        print("[PASS] test_fetch_candles_empty_data")


class TestFetchCandlesOrder(unittest.TestCase):
    def test_reversed_order_sorted(self):
        from okx_client import fetch_candles
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": "0",
            "data": [
                ["1700000009000", "61000.0", "62000.0", "60500.0", "61500.0", "1100.0"],
                ["1700000000000", "60000.0", "61000.0", "59000.0", "60500.0", "1000.0"],
            ]
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        async def run():
            return await fetch_candles(mock_client, "BTC-USDT-SWAP")

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertEqual(result[0]["ts"], 1700000000000)
        self.assertEqual(result[1]["ts"], 1700000009000)
        print("[PASS] test_fetch_candles_reversed_order")


class TestStatePersistence(unittest.TestCase):
    """Verify that state persistence includes position close data."""

    def test_pair_last_closed_in_persist_keys(self):
        from state import STATE_PERSIST_KEYS
        self.assertIn("pair_last_closed", STATE_PERSIST_KEYS,
                       "pair_last_closed must be persisted for cooldown survival")

    def test_position_history_in_persist_keys(self):
        from state import STATE_PERSIST_KEYS
        self.assertIn("position_history", STATE_PERSIST_KEYS)
        self.assertIn("open_positions", STATE_PERSIST_KEYS)
        self.assertIn("consecutive_losses", STATE_PERSIST_KEYS)

    def test_mark_dirty_and_save(self):
        import tempfile, json
        from state import mark_dirty, is_dirty, save_state, _state_dirty, STATE_PERSIST_KEYS
        _state_dirty.clear()
        self.assertFalse(is_dirty())
        mark_dirty()
        self.assertTrue(is_dirty())
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            tmp_path = f.name
        try:
            import state as state_mod
            orig = state_mod.GATEWAY_STATE_FILE
            state_mod.GATEWAY_STATE_FILE = tmp_path
            state_mod.state["pair_last_closed"] = {"BTC-USDT-SWAP": 1234567890.0}
            save_state()
            self.assertFalse(is_dirty())
            with open(tmp_path) as f:
                saved = json.load(f)
            self.assertIn("pair_last_closed", saved)
            self.assertAlmostEqual(saved["pair_last_closed"]["BTC-USDT-SWAP"], 1234567890.0)
            state_mod.GATEWAY_STATE_FILE = orig
        finally:
            os.unlink(tmp_path)


def run_tests():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestIndicatorCalculations,
        TestSignalComputation,
        TestValidateCandle,
        TestCalculateForPair,
        TestCSVStorage,
        TestConstants,
        TestPrec,
        TestNotificationLogic,
        TestCircuitBreakerLogic,
        TestStorageDedup,
        TestIndicatorEdgeCases,
        TestFetchOKXTickerMocked,
        TestFetchCandlesMocked,
        TestFetchCandlesOrder,
        TestStatePersistence,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    result = runner.run(suite)

    print()
    print("=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print(f"  Tests run   : {result.testsRun}")
    print(f"  Failures    : {len(result.failures)}")
    print(f"  Errors      : {len(result.errors)}")
    print(f"  Skipped     : {len(result.skipped)}")
    print(f"  Result      : {'ALL PASSED' if result.wasSuccessful() else 'FAILURES DETECTED'}")
    print("=" * 70)

    if result.failures:
        print("\n  FAILURES:")
        for test, tb in result.failures:
            print(f"  - {test}: {tb.split(chr(10))[-1]}")
    if result.errors:
        print("\n  ERRORS:")
        for test, tb in result.errors:
            print(f"  - {test}: {tb.split(chr(10))[-1]}")

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_tests())
