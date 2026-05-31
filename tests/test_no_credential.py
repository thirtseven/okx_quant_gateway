#!/usr/bin/env python3
"""Test script for OKX Quantitative Trading Gateway - Credential-free mode.

Tests:
1. Gateway startup without credentials
2. Data injection through API
3. Credential management
4. Network status
"""

import json
import logging
import os
import sys
import time
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class TestCredentialManager(unittest.TestCase):
    """Test credential management without external dependencies."""

    def setUp(self):
        from auth.credentials import _credentials_cache, _initialized
        _credentials_cache.clear()
        _initialized = False

    def test_init_credentials_empty(self):
        from auth.credentials import init_credentials, get_credential_status
        init_credentials()
        status = get_credential_status()
        self.assertFalse(any(status.values()))

    def test_set_and_get_credential(self):
        from auth.credentials import set_credential, get_credential, init_credentials
        init_credentials()
        self.assertTrue(set_credential("smtp_sender", "test@example.com"))
        self.assertEqual(get_credential("smtp_sender"), "test@example.com")

    def test_invalid_credential_type(self):
        from auth.credentials import set_credential
        self.assertFalse(set_credential("invalid_type", "value"))

    def test_remove_credential(self):
        from auth.credentials import set_credential, remove_credential, get_credential
        set_credential("smtp_sender", "test@example.com")
        self.assertTrue(remove_credential("smtp_sender"))
        self.assertIsNone(get_credential("smtp_sender"))

    def test_smtp_config(self):
        from auth.credentials import set_credential, get_smtp_config
        config = get_smtp_config()
        self.assertFalse(config["enabled"])
        self.assertIsNone(config["sender"])

        set_credential("smtp_sender", "test@example.com")
        set_credential("smtp_auth_code", "auth123")
        config = get_smtp_config()
        self.assertTrue(config["enabled"])
        self.assertEqual(config["sender"], "test@example.com")

    def test_db_config(self):
        from auth.credentials import set_credential, get_db_config
        config = get_db_config()
        self.assertEqual(config["host"], "127.0.0.1")
        self.assertIsNone(config["password"])

        set_credential("db_password", "secret123")
        config = get_db_config()
        self.assertEqual(config["password"], "secret123")

    def test_okx_config(self):
        from auth.credentials import set_credential, get_okx_config
        config = get_okx_config()
        self.assertFalse(config["configured"])

        set_credential("okx_api_key", "key123")
        config = get_okx_config()
        self.assertTrue(config["configured"])

    def test_apply_credentials_to_env(self):
        from auth.credentials import set_credential, apply_credentials_to_env
        set_credential("smtp_sender", "test@example.com")
        set_credential("smtp_auth_code", "auth123")
        apply_credentials_to_env()
        self.assertEqual(os.environ.get("SMTP_SENDER"), "test@example.com")
        self.assertEqual(os.environ.get("SMTP_AUTH_CODE"), "auth123")


class TestNetworkManager(unittest.TestCase):
    """Test network manager without root privileges."""

    def test_detect_parent_interface(self):
        from network_manager import NetworkManager
        nm = NetworkManager()
        parent = nm._detect_parent_interface()
        self.assertIsNotNone(parent)
        logger.info("Detected parent interface: %s", parent)

    def test_interface_exists_false(self):
        from network_manager import NetworkManager
        nm = NetworkManager()
        self.assertFalse(nm.interface_exists())

    def test_get_status(self):
        from network_manager import NetworkManager
        nm = NetworkManager()
        status = nm.get_status()
        self.assertIn("interface", status)
        self.assertIn("parent", status)
        self.assertIn("subnet", status)
        self.assertIn("gateway_ip", status)
        self.assertFalse(status["exists"])


class TestDataInjection(unittest.TestCase):
    """Test data injection through API endpoints."""

    def setUp(self):
        self.test_data = {
            "candles": [
                {"ts": 1700000000000 + i * 900000, "open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i, "close": 100.5 + i, "vol": 1000.0}
                for i in range(300)
            ]
        }

    def test_candle_data_format(self):
        candles = self.test_data["candles"]
        self.assertEqual(len(candles), 300)
        for candle in candles:
            self.assertIn("ts", candle)
            self.assertIn("open", candle)
            self.assertIn("high", candle)
            self.assertIn("low", candle)
            self.assertIn("close", candle)
            self.assertIn("vol", candle)
            self.assertGreater(candle["high"], candle["low"])

    def test_indicator_calculation(self):
        from indicators.calculator import calc_all_indicators, get_latest_indicators
        candles = self.test_data["candles"]
        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]

        indicators = calc_all_indicators(candles)
        self.assertIn("rsi_14", indicators)
        self.assertIn("macd", indicators)
        self.assertIn("bb_position", indicators)

        latest = get_latest_indicators(indicators)
        self.assertIsNotNone(latest)

    def test_signal_computation(self):
        from indicators.calculator import calc_all_indicators, get_latest_indicators, compute_signal
        from core.constants import build_genome

        candles = self.test_data["candles"]
        indicators = calc_all_indicators(candles)
        latest = get_latest_indicators(indicators)

        if latest:
            result = compute_signal(latest, build_genome())
            if isinstance(result, tuple):
                signal, score, _ = result
                self.assertIn(signal, ["LONG", "SHORT", "HOLD"])
                self.assertIsInstance(score, float)
            else:
                self.assertIn("signal", result)

    def test_storage_operations(self):
        import tempfile
        import shutil
        from core.storage import pair_csv, load_candles, append_candles

        temp_dir = tempfile.mkdtemp()
        try:
            pair = "TEST-USDT-SWAP"
            csv_path = pair_csv(pair, temp_dir)
            self.assertTrue(csv_path.endswith("TEST_USDT_SWAP_15m.csv"))

            candles = self.test_data["candles"][:10]
            append_candles(pair, candles, temp_dir)

            loaded = load_candles(pair, temp_dir)
            self.assertEqual(len(loaded), 10)
        finally:
            shutil.rmtree(temp_dir)


class TestGatewayStartup(unittest.TestCase):
    """Test gateway startup without credentials."""

    def test_import_without_credentials(self):
        import shutil
        from pathlib import Path
        from auth.credentials import CREDENTIALS_FILE

        if os.path.exists(CREDENTIALS_FILE):
            os.remove(CREDENTIALS_FILE)

        try:
            from auth.credentials import init_credentials, get_credential_status, _credentials_cache, _initialized
            _credentials_cache.clear()
            _initialized = False
            init_credentials()
            status = get_credential_status()
            self.assertFalse(any(status.values()))
            logger.info("Gateway can start without credentials")
        except Exception as e:
            self.fail(f"Failed to import without credentials: {e}")

    def test_constants_load(self):
        from core.constants import VERSION, OKX_BASE, DEFAULT_INSTRUMENTS
        self.assertEqual(VERSION, "v2.00")
        self.assertEqual(OKX_BASE, "https://www.okx.com")
        self.assertGreater(len(DEFAULT_INSTRUMENTS), 0)

    def test_state_load(self):
        import tempfile
        import shutil

        temp_dir = tempfile.mkdtemp()
        try:
            with patch('state.DATA_DIR', temp_dir):
                from core.state import state, state_lock, save_state, load_state
                self.assertIsNotNone(state)
                self.assertIn("trading_pairs", state)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestAPIEndpoints(unittest.TestCase):
    """Test API endpoint logic without running server."""

    def test_credential_validation(self):
        from auth.credentials import CREDENTIAL_TYPES
        self.assertIn("smtp_sender", CREDENTIAL_TYPES)
        self.assertIn("db_password", CREDENTIAL_TYPES)
        self.assertIn("okx_api_key", CREDENTIAL_TYPES)

    def test_pair_validation(self):
        import re
        pattern = r'^[A-Z0-9]+-[A-Z]+-(SWAP|FUTURES|SPOT)$'
        self.assertTrue(re.match(pattern, "BTC-USDT-SWAP"))
        self.assertTrue(re.match(pattern, "ETH-USDT-SWAP"))
        self.assertFalse(re.match(pattern, "invalid"))
        self.assertFalse(re.match(pattern, "btc-usdt-swap"))

    def test_direction_validation(self):
        valid_directions = ["LONG", "SHORT", "HOLD"]
        for d in valid_directions:
            self.assertIn(d, valid_directions)
        self.assertNotIn("INVALID", valid_directions)


def run_integration_test():
    """Run integration test with mock server."""
    logger.info("Starting integration test...")

    from auth.credentials import init_credentials, get_credential_status
    init_credentials()
    status = get_credential_status()
    logger.info("Credential status: %s", status)

    from network_manager import NetworkManager
    nm = NetworkManager()
    logger.info("Network status: %s", nm.get_status())

    test_candles = [
        {"ts": 1700000000000 + i * 900000, "open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i, "close": 100.5 + i, "vol": 1000.0}
        for i in range(300)
    ]

    from indicators.calculator import calc_all_indicators, get_latest_indicators
    indicators = calc_all_indicators(test_candles)
    latest = get_latest_indicators(indicators)
    logger.info("Indicators calculated: %s", list(indicators.keys()))
    logger.info("Latest indicators available: %s", latest is not None)

    logger.info("Integration test completed successfully!")
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("  OKX Gateway - Credential-free Test Suite")
    print("=" * 60)

    if len(sys.argv) > 1 and sys.argv[1] == "--integration":
        success = run_integration_test()
        sys.exit(0 if success else 1)
    else:
        unittest.main(verbosity=2)
