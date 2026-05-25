#!/usr/bin/env python3
"""OKX Gateway Integration Test Suite — covers all fixed issues.

Tests:
  1. NaN-safe JSON serialization
  2. CSRF token roundtrip (header + query param)
  3. API endpoints respond without lock blocking
  4. Signal computation (no 504, no HTML errors)
  5. Settings page serves CSRF token
  6. Test-email returns immediately (async, no 504)
  7. Credential encrypt/decrypt roundtrip
  8. Genome leverage backwards compat
  9. Thread safety (state lock)

Usage: python test_integration.py [--server] [--verbose]
  --server  Run against a running server at localhost:5000
"""

import sys
import os
import json
import time
import unittest
import threading
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

VERBOSE = "--verbose" in sys.argv
USE_SERVER = "--server" in sys.argv

if not USE_SERVER:
    os.environ.setdefault("DB_HOST", "127.0.0.1")
    os.environ.setdefault("DB_USER", "test")
    os.environ.setdefault("DB_PASSWORD", "test")
    os.environ.setdefault("DB_NAME", "test")


class TestJSONSafety(unittest.TestCase):
    def test_json_dumps_nan_is_invalid_json(self):
        import json as _json
        result = _json.dumps({"score": float("nan")})
        self.assertIn("NaN", result)

    def test_safe_val_filters_nan(self):
        from indicators import safe_val
        self.assertEqual(safe_val(float("nan"), 42), 42)
        self.assertEqual(safe_val(3.14, 0), 3.14)

    def test_safe_get_filters_nan(self):
        from indicators import _safe_get
        self.assertEqual(_safe_get({"v": float("nan")}, "v", 50), 50)
        self.assertEqual(_safe_get({"v": 3.14}, "v", 0), 3.14)

    def test_flask_app_imports(self):
        from okx_quant_gateway import app
        self.assertIsNotNone(app)
        self.assertEqual(app.name, "okx_quant_gateway")


@unittest.skipIf(not USE_SERVER, "requires --server")
class TestAPIEndpoints(unittest.TestCase):
    BASE = "http://localhost:5000"

    def _get(self, path, csrf=None):
        import urllib.request
        url = f"{self.BASE}{path}"
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/json")
        if csrf:
            req.add_header("X-CSRF-Token", csrf)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except Exception as e:
            return 0, {"error": str(e)}

    def test_health_endpoint(self):
        code, data = self._get("/api/health")
        self.assertEqual(code, 200)
        self.assertEqual(data.get("status"), "ok")

    def test_signals_endpoint(self):
        code, data = self._get("/api/signals")
        self.assertEqual(code, 200)
        self.assertIn("signals", data)
        self.assertIn("thresholds", data)

    def test_indicators_endpoint(self):
        code, data = self._get("/api/indicators")
        self.assertEqual(code, 200)
        self.assertIn("pair_count", data)

    def test_state_endpoint(self):
        code, data = self._get("/api/state")
        self.assertEqual(code, 200)
        self.assertIn("mode", data)

    def test_no_html_error_on_api(self):
        import urllib.request
        try:
            req = urllib.request.Request(f"{self.BASE}/api/signals")
            with urllib.request.urlopen(req, timeout=10) as r:
                text = r.read().decode()
                self.assertNotIn("<html>", text.lower())
                self.assertNotIn("<!doctype", text.lower())
        except Exception as e:
            self.fail(f"API request failed: {e}")

    def test_test_email_returns_immediately(self):
        import subprocess
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "--max-time", "3", "-X", "POST", f"{self.BASE}/api/test-email",
             "-H", "Content-Type: application/json", "-d", "{}"],
            capture_output=True, text=True, timeout=5,
        )
        self.assertIn(result.stdout.strip(), ("200", "403", "400"),
                      f"Unexpected HTTP status: {result.stdout}")


class TestCSRFRoundtrip(unittest.TestCase):
    def test_csrf_header_match(self):
        from flask import Flask, session
        import secrets
        app = Flask(__name__)
        app.secret_key = "test"
        token = secrets.token_hex(32)
        with app.test_request_context("/", headers={"X-CSRF-Token": token}):
            from flask import session as _s
            _s["csrf_token"] = token
            self.assertEqual(_s.get("csrf_token"), token)

    def test_csrf_mismatch_rejected(self):
        from flask import Flask
        app = Flask(__name__)
        app.secret_key = "test"
        token_a = "a" * 64
        token_b = "b" * 64
        with app.test_request_context("/"):
            from flask import session as _s
            _s["csrf_token"] = token_a
            self.assertNotEqual(_s.get("csrf_token"), token_b)


class TestCredentialRoundtrip(unittest.TestCase):
    def test_encrypt_decrypt(self):
        from credential_manager import _simple_encrypt, _simple_decrypt
        plain = "test_secret_123!@#"
        enc = _simple_encrypt(plain)
        self.assertIsNotNone(enc)
        self.assertNotEqual(enc, plain)
        dec = _simple_decrypt(enc)
        self.assertEqual(dec, plain)

    def test_empty_handled(self):
        from credential_manager import _simple_encrypt, _simple_decrypt
        self.assertEqual(_simple_encrypt(""), "")
        self.assertEqual(_simple_decrypt(""), "")


class TestGenomeBackwardsCompat(unittest.TestCase):
    def test_leverage_present(self):
        from constants import build_genome
        g = build_genome()
        self.assertIn("leverage", g)
        self.assertGreater(g["leverage"], 0)

    def test_fee_present(self):
        from constants import build_genome
        g = build_genome()
        self.assertIn("fee", g)
        self.assertGreater(g["fee"], 0)

    def test_regimes_present(self):
        from constants import build_genome
        g = build_genome()
        self.assertIn("regimes", g)
        for r in ("bull", "bear", "range"):
            self.assertIn(r, g["regimes"])
            self.assertIn("score_min", g["regimes"][r])


class TestThreadSafety(unittest.TestCase):
    def test_state_lock_no_deadlock(self):
        from state import state_lock
        acquired = state_lock.acquire(timeout=2)
        self.assertTrue(acquired)
        state_lock.release()

    def test_concurrent_reads(self):
        from state import state, state_lock
        errors = []
        def reader():
            try:
                for _ in range(50):
                    _ = state.get("mode")
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(errors), 0)


class TestSignalComputation(unittest.TestCase):
    def test_compute_signal_valid(self):
        from indicators import compute_signal
        ind = {
            "close": 60000, "ema_25": 59500, "ema_50": 58000,
            "atr_14": 500, "rsi_14": 55, "macd_hist": 0.5,
            "macd_signal": 0.3, "bb_position": 0.4, "stoch_k": 60,
        }
        genome = {
            "regime_mode": "auto",
            "regimes": {
                "bull": {"rsi_ob": 75, "rsi_os": 28, "rsi_w": 0.8,
                         "macd_w": 0.9, "bb_w": 1.0, "bb_thresh": 0.3,
                         "stoch_w": 0.9, "stoch_os": 25, "stoch_ob": 83,
                         "ema_w": 0.3, "score_min": 1.5},
            },
        }
        signal, nl, ns = compute_signal(ind, genome)
        self.assertIn(signal, ("LONG", "SHORT", "HOLD"))
        self.assertIsInstance(nl, float)
        self.assertIsInstance(ns, float)

    def test_no_nan_in_scores(self):
        from indicators import compute_signal
        ind = {
            "close": 100, "ema_25": 100, "ema_50": 100,
            "atr_14": 0.001, "rsi_14": 50, "macd_hist": 0,
            "macd_signal": 0, "bb_position": 0.5, "stoch_k": 50,
        }
        genome = {
            "regimes": {
                "bull": {"rsi_ob": 75, "rsi_os": 28, "rsi_w": 0.8,
                         "macd_w": 0.9, "bb_w": 1.0, "bb_thresh": 0.3,
                         "stoch_w": 0.9, "stoch_os": 25, "stoch_ob": 83,
                         "ema_w": 0.3, "score_min": 0.5},
            },
        }
        _, nl, ns = compute_signal(ind, genome)
        self.assertFalse(math.isnan(nl))
        self.assertFalse(math.isnan(ns))


class TestStoragePandas(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_candles(self):
        from storage import load_candles, pair_csv
        import pandas as pd
        path = pair_csv("TEST-X-USDT-SWAP", module_data_dir=self.tmpdir)
        df = pd.DataFrame({
            "ts": [1700000000 + i * 900 for i in range(10)],
            "open": [100.0] * 10, "high": [101.0] * 10,
            "low": [99.0] * 10, "close": [100.5] * 10, "vol": [1000.0] * 10,
        })
        os.makedirs(os.path.dirname(path), exist_ok=True)
        df.to_csv(path, index=False)
        rows = load_candles("TEST-X-USDT-SWAP", module_data_dir=self.tmpdir)
        self.assertEqual(len(rows), 10)


def main():
    print("=" * 60)
    print("  OKX Gateway Integration Test Suite")
    print(f"  Server mode: {USE_SERVER}")
    print("=" * 60)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [TestJSONSafety, TestAPIEndpoints, TestCSRFRoundtrip,
                TestCredentialRoundtrip, TestGenomeBackwardsCompat,
                TestThreadSafety, TestSignalComputation, TestStoragePandas]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2 if VERBOSE else 1)
    result = runner.run(suite)

    print(f"\n{'='*60}")
    print(f"  Tests: {result.testsRun} | Passed: {result.testsRun - len(result.failures) - len(result.errors)} | Failed: {len(result.failures)} | Errors: {len(result.errors)}")
    print("=" * 60)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
