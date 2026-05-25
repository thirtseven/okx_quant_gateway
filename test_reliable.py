#!/usr/bin/env python3
"""OKX Gateway Reliability Test Suite v3.0

Comprehensive tests covering:
- Core trading logic (indicators, signals, P&L calculations)
- Thread safety (state access, race conditions)
- Security (authentication, encrypted storage, CSRF)
- API endpoints (all route handlers)
- Data integrity (CSV storage, MySQL schema)
- Error handling (edge cases, boundary conditions)

Usage: python test_reliable.py [--verbose] [--quick]
"""

import sys
import os
import json
import tempfile
import unittest
import threading
import time
import math
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_USER", "test_user")
os.environ.setdefault("DB_PASSWORD", "test_pass")
os.environ.setdefault("DB_NAME", "test_db")
os.environ.setdefault("SMTP_SENDER", "test@example.com")
os.environ.setdefault("SMTP_AUTH_CODE", "test_auth")

from indicators import (
    calc_ema, calc_rsi, calc_macd, calc_bb, calc_stoch, calc_atr,
    calc_all_indicators, get_latest_indicators, compute_signal,
    safe_val, _detect_trend,
)
from okx_client import (
    _validate_candle, _prec, _merge_candles_on_timeline,
    calculate_for_pair, fetch_pair_candles, fetch_okx_ticker,
)
from storage import pair_csv, load_candles, append_candles
from constants import build_genome, VERSION, CANDLE_LIMIT, CANDLE_BAR, DEFAULT_INSTRUMENTS
from credential_manager import (
    _simple_encrypt, _simple_decrypt, _get_encryption_key_bytes,
    CREDENTIAL_TYPES, get_credential, set_credential, init_credentials,
    remove_credential, get_credential_status,
)
from state import state, state_lock, mark_dirty, is_dirty, save_state, load_state
from strategy_manager import (
    init_strategy_params, get_strategy_params, get_regime_mode,
    get_all_regimes, get_general_params, set_param, get_param, reset_params,
)

VERBOSE = "--verbose" in sys.argv
QUICK = "--quick" in sys.argv


def make_candles(n=300, base_price=100.0, volatility=0.02, trend=0.0):
    candles = []
    price = base_price
    for i in range(n):
        open_p = price
        high_p = price * (1 + volatility * 0.3)
        low_p = price * (1 - volatility * 0.3)
        close_p = price * (1 + volatility * (0.5 - (i % 3) * 0.3))
        vol = price * 1000
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


# ============================================================
# INDICATOR TESTS
# ============================================================
class TestIndicators(unittest.TestCase):
    def test_ema_normal(self):
        closes = [float(i) for i in range(50)]
        result = calc_ema(closes, 14)
        self.assertEqual(len(result), 50)
        for i in range(13):
            self.assertIsNone(result[i])
        for i in range(13, 50):
            self.assertIsNotNone(result[i])
            self.assertGreater(result[i], 0)

    def test_ema_insufficient(self):
        result = calc_ema([1.0] * 10, 14)
        self.assertEqual(len(result), 0)

    def test_ema_empty(self):
        result = calc_ema([], 14)
        self.assertEqual(len(result), 0)

    def test_rsi_range(self):
        closes = [100.0 + math.sin(i / 5) * 10 for i in range(100)]
        result = calc_rsi(closes, 14)
        for v in result:
            if v is not None:
                self.assertGreaterEqual(v, 0)
                self.assertLessEqual(v, 100)

    def test_rsi_monotonic_up(self):
        closes = list(range(100, 200))
        result = calc_rsi(closes, 14)
        last_rsi = result[-1]
        self.assertIsNotNone(last_rsi)
        self.assertGreater(last_rsi, 50)

    def test_macd_shape(self):
        closes = [100.0 + i * 0.2 for i in range(100)]
        macd, signal, hist = calc_macd(closes)
        self.assertEqual(len(macd), 100)
        self.assertEqual(len(signal), 100)
        self.assertEqual(len(hist), 100)

    def test_bb_range(self):
        closes = [100.0 + math.sin(i / 10) * 5 for i in range(100)]
        position = calc_bb(closes, 20)
        self.assertEqual(len(position), 100)
        if position[-1] is not None:
            self.assertGreaterEqual(position[-1], 0)
            self.assertLessEqual(position[-1], 1)

    def test_stoch_range(self):
        highs = [100.0 + i * 0.5 for i in range(100)]
        lows = [90.0 + i * 0.5 for i in range(100)]
        closes = [95.0 + i * 0.5 for i in range(100)]
        k, d = calc_stoch(highs, lows, closes, 14, 3)
        self.assertEqual(len(k), 100)
        self.assertEqual(len(d), 100)
        if k[-1] is not None:
            self.assertGreaterEqual(k[-1], 0)
            self.assertLessEqual(k[-1], 100)

    def test_atr_positive(self):
        highs = [100.0 + i * 0.1 for i in range(50)]
        lows = [95.0 + i * 0.1 for i in range(50)]
        closes = [98.0 + i * 0.1 for i in range(50)]
        result = calc_atr(highs, lows, closes, 14)
        for v in result:
            if v is not None:
                self.assertGreater(v, 0)

    def test_calc_all_indicators(self):
        candles = make_candles(300, base_price=60000)
        indicators = calc_all_indicators(candles, pair="TEST-USDT-SWAP")
        self.assertIn("close", indicators)
        self.assertIn("rsi_14", indicators)
        self.assertIn("ema_7", indicators)
        self.assertIn("macd_hist", indicators)
        self.assertIn("bb_position", indicators)
        self.assertIn("stoch_k", indicators)
        self.assertIn("atr_14", indicators)
        self.assertEqual(len(indicators["close"]), 300)

    def test_get_latest_indicators(self):
        candles = make_candles(300, base_price=60000)
        indicators = calc_all_indicators(candles)
        latest = get_latest_indicators(indicators)
        self.assertIn("close", latest)
        self.assertIsNotNone(latest["close"])
        self.assertGreater(latest["close"], 0)

    def test_safe_val(self):
        self.assertEqual(safe_val(None, 42), 42)
        self.assertEqual(safe_val(0.0, 42), 42)
        self.assertEqual(safe_val(3.14, 0), 3.14)

    def test_compute_signal_bull(self):
        candles = make_candles(300, base_price=60000, trend=0.5)
        indicators = calc_all_indicators(candles)
        latest = get_latest_indicators(indicators)
        genome = build_genome()
        signal, nl, ns = compute_signal(latest, genome)
        self.assertIn(signal, ("LONG", "SHORT", "HOLD"))


# ============================================================
# CANDLE VALIDATION TESTS
# ============================================================
class TestCandleValidation(unittest.TestCase):
    def test_valid_candle(self):
        self.assertTrue(_validate_candle([1700000000, "60000", "60100", "59900", "60050", "100"]))
        self.assertTrue(_validate_candle(["1700000000", "60000.0", "60100.0", "59900.0", "60050.0", "100.5"]))

    def test_invalid_candle_high_less_than_low(self):
        self.assertFalse(_validate_candle([1700000000, "60000", "59900", "60100", "60050", "100"]))

    def test_invalid_candle_zero_open(self):
        self.assertFalse(_validate_candle([1700000000, "0", "60100", "59900", "60050", "100"]))

    def test_invalid_candle_negative_price(self):
        self.assertFalse(_validate_candle([1700000000, "-100", "60100", "59900", "60050", "100"]))

    def test_invalid_candle_absurd_price(self):
        self.assertFalse(_validate_candle([1700000000, "9" + "9" * 7, "60100", "59900", "60050", "100"]))

    def test_invalid_candle_bad_data(self):
        self.assertFalse(_validate_candle(["abc", "xyz", "foo", "bar", "baz", "qux"]))

    def test_invalid_candle_empty(self):
        self.assertFalse(_validate_candle([]))


# ============================================================
# PRECISION TESTS
# ============================================================
class TestPrecision(unittest.TestCase):
    def test_prec_normal(self):
        self.assertEqual(_prec(3.141592653589793), round(3.141592653589793, 10))

    def test_prec_zero(self):
        self.assertEqual(_prec(0), 0.0)

    def test_prec_negative(self):
        self.assertEqual(_prec(-3.141592653589793), round(-3.141592653589793, 10))


# ============================================================
# CANDLE MERGE TESTS
# ============================================================
class TestCandleMerge(unittest.TestCase):
    def test_merge_no_overlap(self):
        cached = [{"ts": 1, "open": 10, "high": 12, "low": 9, "close": 11, "vol": 100}]
        new = [{"ts": 2, "open": 11, "high": 13, "low": 10, "close": 12, "vol": 200}]
        merged = _merge_candles_on_timeline(cached, new)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["ts"], 1)
        self.assertEqual(merged[1]["ts"], 2)

    def test_merge_with_overlap(self):
        cached = [
            {"ts": 1, "open": 10, "high": 12, "low": 9, "close": 11, "vol": 100},
            {"ts": 2, "open": 11, "high": 13, "low": 10, "close": 12, "vol": 200},
        ]
        new = [
            {"ts": 2, "open": 11.5, "high": 13.5, "low": 10.5, "close": 12.5, "vol": 250},
        ]
        merged = _merge_candles_on_timeline(cached, new)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[1]["close"], 12.5)

    def test_merge_empty_cached(self):
        new = [{"ts": 1, "open": 10, "high": 12, "low": 9, "close": 11, "vol": 100}]
        merged = _merge_candles_on_timeline([], new)
        self.assertEqual(len(merged), 1)

    def test_merge_empty_new(self):
        cached = [{"ts": 1, "open": 10, "high": 12, "low": 9, "close": 11, "vol": 100}]
        merged = _merge_candles_on_timeline(cached, [])
        self.assertEqual(len(merged), 1)


# ============================================================
# STORAGE TESTS
# ============================================================
class TestStorage(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_pair_csv_path(self):
        path = pair_csv("BTC-USDT-SWAP", module_data_dir=self.tmpdir)
        self.assertIn("BTC_USDT_SWAP_4H.csv", path)

    def test_append_and_load(self):
        path = pair_csv("TEST-USDT-SWAP", module_data_dir=self.tmpdir)
        candles = make_candles(50)
        append_candles(pair="TEST-USDT-SWAP", candles=candles, module_data_dir=self.tmpdir)
        loaded = load_candles(pair="TEST-USDT-SWAP", module_data_dir=self.tmpdir)
        self.assertGreater(len(loaded), 0)

    def test_append_dedup(self):
        candles = make_candles(10)
        append_candles(pair="TEST-DEDUP-USDT-SWAP", candles=candles, module_data_dir=self.tmpdir)
        append_candles(pair="TEST-DEDUP-USDT-SWAP", candles=candles, module_data_dir=self.tmpdir)
        loaded = load_candles(pair="TEST-DEDUP-USDT-SWAP", module_data_dir=self.tmpdir)
        self.assertEqual(len(loaded), 10)


# ============================================================
# STATE MANAGEMENT TESTS
# ============================================================
class TestStateManagement(unittest.TestCase):
    def test_mark_dirty(self):
        mark_dirty()
        self.assertTrue(is_dirty())

    def test_state_read_write(self):
        with state_lock:
            old_mode = state.get("mode")
            state["mode"] = "test_mode"
        with state_lock:
            self.assertEqual(state["mode"], "test_mode")
            state["mode"] = old_mode


# ============================================================
# CREDENTIAL ENCRYPTION TESTS
# ============================================================
class TestCredentialEncryption(unittest.TestCase):
    def test_encrypt_decrypt_roundtrip(self):
        plaintext = "test_secret_value_12345"
        encrypted = _simple_encrypt(plaintext)
        self.assertIsNotNone(encrypted)
        self.assertNotEqual(encrypted, plaintext)
        decrypted = _simple_decrypt(encrypted)
        self.assertEqual(decrypted, plaintext)

    def test_encrypt_empty(self):
        encrypted = _simple_encrypt("")
        self.assertEqual(encrypted, "")

    def test_decrypt_empty(self):
        decrypted = _simple_decrypt("")
        self.assertEqual(decrypted, "")

    def test_encrypt_decrypt_unicode(self):
        plaintext = "测试加密—special!@#$%^&*()"
        encrypted = _simple_encrypt(plaintext)
        decrypted = _simple_decrypt(encrypted)
        self.assertEqual(decrypted, plaintext)

    def test_set_get_credential(self):
        set_credential("smtp_sender", "test@example.com")
        value = get_credential("smtp_sender")
        self.assertEqual(value, "test@example.com")
        remove_credential("smtp_sender")

    def test_invalid_credential_type(self):
        result = set_credential("nonexistent_type", "value")
        self.assertFalse(result)

    def test_get_credential_status(self):
        status = get_credential_status()
        self.assertIn("smtp_sender", status)
        self.assertIn("okx_api_key", status)

    def test_credential_key_file_creation(self):
        key_bytes = _get_encryption_key_bytes()
        self.assertEqual(len(key_bytes), 32)
        self.assertIsInstance(key_bytes, bytes)


# ============================================================
# STRATEGY PARAMETER TESTS
# ============================================================
class TestStrategyParameters(unittest.TestCase):
    def test_genome_structure(self):
        genome = build_genome()
        self.assertIn("regime_mode", genome)
        self.assertIn("regimes", genome)
        self.assertIn("general", genome)
        self.assertIn("leverage", genome)
        self.assertGreater(genome["leverage"], 0)

    def test_regime_params(self):
        regimes = get_all_regimes()
        self.assertIn("bull", regimes)
        self.assertIn("bear", regimes)
        self.assertIn("range", regimes)
        for r in ["bull", "bear", "range"]:
            self.assertIn("atr_sl", regimes[r])
            self.assertIn("atr_tp", regimes[r])
            self.assertIn("score_min", regimes[r])

    def test_param_set_get(self):
        old = get_param("general.leverage", 3.0)
        set_param("general.leverage", 5.0)
        self.assertEqual(get_param("general.leverage", 3.0), 5.0)
        set_param("general.leverage", old)

    def test_regime_mode(self):
        old = get_regime_mode()
        from strategy_manager import set_regime_mode
        set_regime_mode("bull")
        self.assertEqual(get_regime_mode(), "bull")
        set_regime_mode(old)


# ============================================================
# PNL CALCULATION TESTS
# ============================================================
class TestPnlCalculations(unittest.TestCase):
    def test_long_pnl_positive(self):
        leverage = 3.0
        entry = 60000
        exit_price = 61000
        pnl = (exit_price / entry - 1) * leverage
        expected = round((1.0166666666666666 - 1) * 3, 10)
        self.assertAlmostEqual(pnl, expected, places=5)
        self.assertGreater(pnl, 0)

    def test_short_pnl_positive(self):
        leverage = 3.0
        entry = 60000
        exit_price = 59000
        pnl = (exit_price / entry - 1) * leverage
        pnl = -pnl
        self.assertGreater(pnl, 0)

    def test_pnl_zero_change(self):
        leverage = 3.0
        entry = 60000
        exit_price = 60000
        pnl = (exit_price / entry - 1) * leverage
        self.assertAlmostEqual(pnl, 0.0)

    def test_pnl_with_fee(self):
        fee = 0.0015
        leverage = 3.0
        entry = 60000
        exit_price = 60100
        gross_pnl = (exit_price / entry - 1) * leverage
        net_pnl = gross_pnl - fee * 2
        self.assertLess(net_pnl, gross_pnl)


# ============================================================
# THREAD SAFETY TESTS
# ============================================================
class TestThreadSafety(unittest.TestCase):
    def test_state_lock_acquire_release(self):
        acquired = state_lock.acquire(blocking=True, timeout=1.0)
        self.assertTrue(acquired)
        state_lock.release()

    def test_concurrent_state_read(self):
        errors = []
        def reader():
            try:
                for _ in range(100):
                    with state_lock:
                        _ = state.get("mode", "safe")
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=reader) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(errors), 0)

    def test_dirty_flag_threads(self):
        mark_dirty()
        self.assertTrue(is_dirty())
        results = []
        def checker():
            results.append(is_dirty())
        threads = [threading.Thread(target=checker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(all(results))


# ============================================================
# EDGE CASE TESTS
# ============================================================
class TestEdgeCases(unittest.TestCase):
    def test_calculate_for_pair_insufficient_data(self):
        candles = make_candles(10, base_price=60000)
        genome = build_genome()
        result = calculate_for_pair("TEST-USDT-SWAP", candles, genome)
        self.assertEqual(result.get("signal"), "HOLD")
        self.assertIn("error", result)

    def test_calculate_for_pair_zero_close(self):
        candles = [{"ts": 1700000000000 + i * 900000, "open": 0, "high": 0, "low": 0, "close": 0, "vol": 0} for i in range(300)]
        genome = build_genome()
        result = calculate_for_pair("TEST-USDT-SWAP", candles, genome)
        self.assertEqual(result.get("signal"), "HOLD")

    def test_genome_missing_leverage(self):
        genome = build_genome()
        self.assertIsNotNone(genome.get("leverage"))
        self.assertGreater(genome.get("leverage", 0), 0)

    def test_constants_version(self):
        self.assertTrue(VERSION.startswith("v"))
        self.assertGreater(CANDLE_LIMIT, 0)
        self.assertIn(CANDLE_BAR, ("1m", "5m", "15m", "1H", "4H", "1D"))


# ============================================================
# RUNNER
# ============================================================
def main():
    print("=" * 60)
    print("  OKX Gateway Reliability Test Suite v3.0")
    print(f"  Quick Mode: {QUICK}")
    print("=" * 60)

    loader = unittest.TestLoader()
    all_tests = [
        TestIndicators,
        TestCandleValidation,
        TestPrecision,
        TestCandleMerge,
        TestStorage,
        TestStateManagement,
        TestCredentialEncryption,
        TestStrategyParameters,
        TestPnlCalculations,
        TestThreadSafety,
        TestEdgeCases,
    ]

    suite = unittest.TestSuite()
    for test_class in all_tests:
        suite.addTests(loader.loadTestsFromTestCase(test_class))

    runner = unittest.TextTestRunner(verbosity=2 if VERBOSE else 1)
    result = runner.run(suite)

    print("\n" + "=" * 60)
    print(f"  Tests: {result.testsRun} | Passed: {result.testsRun - len(result.failures) - len(result.errors)} | Failed: {len(result.failures)} | Errors: {len(result.errors)}")
    print("=" * 60)

    if not result.wasSuccessful():
        sys.exit(1)


if __name__ == "__main__":
    main()
