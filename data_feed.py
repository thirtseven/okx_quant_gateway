from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import numpy as np
import pandas as pd
import requests

LOCAL_DATA_DIR: Path = Path(os.environ.get("QUANT_DATA_DIR") or str(Path(__file__).resolve().parent / ".." / "quant-local" / "data" / "okx_4h_4y_indicators"))
OKX_API: str = "https://www.okx.com/api/v5/market/candles"
PROXY: str = "http://127.0.0.1:7891"
PROXIES: dict[str, str] = {"http": PROXY, "https": PROXY}

# ---- OKX API 全局速率限制 (防 429) ----
import threading as _threading

_RATE_LOCK = _threading.Lock()
_RATE_LAST_TS: float = 0.0
_RATE_MIN_INTERVAL: float = 0.15  # 每请求最小间隔 (秒) → 约 6-7 req/s


def _rate_limited() -> None:
    """在两次 OKX API 请求之间强制最小间隔, 避免触发 429 限流."""
    global _RATE_LAST_TS
    with _RATE_LOCK:
        now = time.monotonic()
        wait = _RATE_MIN_INTERVAL - (now - _RATE_LAST_TS)
        if wait > 0:
            time.sleep(wait)
        _RATE_LAST_TS = time.monotonic()
TRACKED_COINS: tuple[str, ...] = (
    "AAVE-USDT", "ADA-USDT", "ALGO-USDT", "APT-USDT", "ARB-USDT",
    "ATOM-USDT", "AVAX-USDT", "BCH-USDT", "BONK-USDT", "BTC-USDT",
    "CRV-USDT", "DOGE-USDT", "DOT-USDT", "DYDX-USDT", "ENS-USDT",
    "ETC-USDT", "ETH-USDT", "FIL-USDT", "FLOKI-USDT", "HBAR-USDT",
    "ICP-USDT", "INJ-USDT", "JUP-USDT", "LDO-USDT", "LINK-USDT",
    "LTC-USDT", "NEAR-USDT", "OP-USDT", "PEPE-USDT", "RENDER-USDT",
    "SEI-USDT", "SHIB-USDT", "SNX-USDT", "SOL-USDT", "STX-USDT",
    "SUI-USDT", "TAO-USDT", "TIA-USDT", "TRX-USDT", "UNI-USDT",
    "WIF-USDT", "WLD-USDT", "XLM-USDT", "XRP-USDT",
)


@dataclass(frozen=True, slots=True)
class Candle:
    timestamp: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float
    coin: str
    confirmed: bool = True  # False=当前未收线 bar (实时展示用, 不进模型)


class OKXFeedError(RuntimeError):
    pass


def okx_to_usdt(coin: str) -> str:
    return coin.replace("-USDT", "")


def fetch_okx_candles(inst_id: str, bar: str = "4H", limit: int = 200, coin_label: str | None = None) -> list[Candle]:
    import json
    import subprocess

    _rate_limited()
    url = f"{OKX_API}?instId={inst_id}&bar={bar}&limit={limit}"
    result = subprocess.run(
        ["curl", "-s", "-x", PROXY, "--max-time", "15", url],
        capture_output=True, text=True, timeout=20,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise OKXFeedError(f"curl failed: {result.stderr.strip()[:120]}")
    payload = json.loads(result.stdout)
    if payload.get("code") == "429":
        raise OKXFeedError("OKX rate-limited (429)")
    if payload.get("code") != "0":
        raise OKXFeedError(f"OKX error {payload.get('code')}: {payload.get('msg')}")
    label = coin_label or inst_id
    candles: list[Candle] = []
    for row in payload["data"]:
        # 保留所有 bar 并标记是否已收线 (confirm=="1"); 未完成 bar 仅用于实时展示
        confirmed = len(row) >= 9 and row[8] == "1"
        try:
            o, h, l, c, vol = float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])
        except (ValueError, TypeError, IndexError):
            continue
        if h < l or o <= 0 or h <= 0 or l <= 0 or c <= 0:
            continue
        if max(abs(o), abs(h), abs(l), abs(c)) > 1e7:
            continue
        candles.append(
            Candle(
                timestamp=pd.Timestamp(int(row[0]), unit="ms", tz="UTC"),
                open=o,
                high=h,
                low=l,
                close=c,
                volume=vol,
                coin=label,
                confirmed=confirmed,
            )
        )
    return list(reversed(candles))


class LiveFeed:
    """Real-time 4h candle feed via Windows-side network bridge.

    Tries OKX first, falls back to Gate.io. PowerShell runs on the Windows
    host (via -EncodedCommand) so WSL network restrictions don't apply.
    """

    OKX_MAP: dict[str, str] = {c: c.replace("-USDT", "-USDT-SWAP") for c in TRACKED_COINS}  # training data is USDT perp
    GATE_MAP: dict[str, str] = {
        "AAVE-USDT": "AAVE_USDT",
        "ADA-USDT": "ADA_USDT",
        "ALGO-USDT": "ALGO_USDT",
        "APT-USDT": "APT_USDT",
        "ARB-USDT": "ARB_USDT",
        "ATOM-USDT": "ATOM_USDT",
        "AVAX-USDT": "AVAX_USDT",
        "BCH-USDT": "BCH_USDT",
        "BONK-USDT": "BONK_USDT",
        "BTC-USDT": "BTC_USDT",
        "CRV-USDT": "CRV_USDT",
        "DOGE-USDT": "DOGE_USDT",
        "DOT-USDT": "DOT_USDT",
        "DYDX-USDT": "DYDX_USDT",
        "ENS-USDT": "ENS_USDT",
        "ETC-USDT": "ETC_USDT",
        "ETH-USDT": "ETH_USDT",
        "FIL-USDT": "FIL_USDT",
        "FLOKI-USDT": "FLOKI_USDT",
        "HBAR-USDT": "HBAR_USDT",
        "ICP-USDT": "ICP_USDT",
        "INJ-USDT": "INJ_USDT",
        "JUP-USDT": "JUP_USDT",
        "LDO-USDT": "LDO_USDT",
        "LINK-USDT": "LINK_USDT",
        "LTC-USDT": "LTC_USDT",
        "NEAR-USDT": "NEAR_USDT",
        "OP-USDT": "OP_USDT",
        "PEPE-USDT": "PEPE_USDT",
        "RENDER-USDT": "RENDER_USDT",
        "SEI-USDT": "SEI_USDT",
        "SHIB-USDT": "SHIB_USDT",
        "SNX-USDT": "SNX_USDT",
        "SOL-USDT": "SOL_USDT",
        "STX-USDT": "STX_USDT",
        "SUI-USDT": "SUI_USDT",
        "TAO-USDT": "TAO_USDT",
        "TIA-USDT": "TIA_USDT",
        "TRX-USDT": "TRX_USDT",
        "UNI-USDT": "UNI_USDT",
        "WIF-USDT": "WIF_USDT",
        "WLD-USDT": "WLD_USDT",
        "XLM-USDT": "XLM_USDT",
        "XRP-USDT": "XRP_USDT",
    }

    def __init__(
        self,
        coins: tuple[str, ...] = TRACKED_COINS,
        prefer: str = "okx",
    ) -> None:
        self.coin_list: tuple[str, ...] = tuple(
            c for c in coins if c in self.OKX_MAP and c in self.GATE_MAP
        )
        self.history_cache: dict[str, pd.DataFrame] = {}
        self.source: str | None = None
        self.last_error: str | None = None
        self.prefer = prefer  # "okx" direct via proxy; "gateio" via PowerShell bridge

    @property
    def coins(self) -> tuple[str, ...]:
        return self.coin_list

    def _fetch_okx_direct(self, coin: str, limit: int) -> list[Candle]:
        inst_id = self.OKX_MAP.get(coin, coin)
        return fetch_okx_candles(inst_id, bar="4H", limit=limit, coin_label=coin)

    def _fetch(self, coin: str, limit: int) -> tuple[list[Candle], str]:
        """Fetch candles via OKX direct (requests + proxy), fallback PowerShell bridge.

        Tries OKX first (through the local clash proxy); on failure falls
        back to Gate.io via Windows PowerShell. Returns (candles, used_source).
        """
        if self.prefer in ("okx", "okx_direct"):
            for attempt in range(3):
                try:
                    candles = self._fetch_okx_direct(coin, limit)
                    if candles:
                        return candles, "okx"
                except OKXFeedError as exc:
                    if "rate-limited" in str(exc).lower() and attempt < 2:
                        time.sleep(2.0 * (attempt + 1))
                        continue
                    if attempt < 2:
                        time.sleep(1.0)
                except Exception:
                    if attempt < 2:
                        time.sleep(1.0)

        import json
        import subprocess

        def _run(url: str) -> list[dict]:
            _rate_limited()
            cmd = (
                "Invoke-RestMethod -Uri '" + url + "' -TimeoutSec 12 | "
                "ConvertTo-Json -Compress -Depth 5"
            )
            result = subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-Command", cmd],
                capture_output=True, timeout=30,
            )
            raw = result.stdout.decode("utf-8", errors="replace").strip()
            if result.returncode != 0 or not raw:
                raise OKXFeedError(
                    result.stderr.decode("utf-8", errors="replace").strip()[:200] or "empty"
                )
            data = json.loads(raw)
            if isinstance(data, dict) and "data" in data:
                if data.get("code") != "0":
                    raise OKXFeedError(f"okx code {data.get('code')}")
                return data["data"]
            if isinstance(data, dict) and "value" in data:
                return data["value"]
            return data

        def _to_candles(rows: list, mode: str) -> list[Candle]:
            out: list[Candle] = []
            for row in rows:
                if mode == "okx":
                    # row: [ts(ms), o, h, l, c, vol, ..., confirm]
                    confirmed = len(row) >= 9 and str(row[8]) == "1"
                    out.append(
                        Candle(
                            timestamp=pd.Timestamp(int(row[0]), unit="ms", tz="UTC"),
                            open=float(row[1]), high=float(row[2]),
                            low=float(row[3]), close=float(row[4]),
                            volume=float(row[5]), coin=coin,
                            confirmed=confirmed,
                        )
                    )
                else:
                    # Gate.io v4 spot candlesticks field order (verified):
                    # [timestamp, quote_volume, close, high, low, open, base_volume, window_close]
                    v = row["value"] if isinstance(row, dict) else row
                    confirmed = len(v) > 7 and str(v[7]) in ("1", "True", "true")
                    out.append(
                        Candle(
                            timestamp=pd.Timestamp(int(v[0]), unit="s", tz="UTC"),
                            open=float(v[5]), high=float(v[3]),
                            low=float(v[4]), close=float(v[2]),
                            volume=float(v[6]), coin=coin,
                            confirmed=confirmed,
                        )
                    )
            return out

        okx_inst = self.OKX_MAP.get(coin, coin)
        okx_url = (
            f"https://www.okx.com/api/v5/market/candles"
            f"?instId={okx_inst}&bar=4H&limit={limit}"
        )
        if self.prefer == "okx":
            try:
                rows = _run(okx_url)
                return _to_candles(rows, "okx"), "okx"
            except Exception:
                pass
        pair = self.GATE_MAP.get(coin, coin.replace("-USDT", "").replace("-", "_") + "_USDT")
        gate_url = (
            f"https://api.gateio.ws/api/v4/spot/candlesticks"
            f"?currency_pair={pair}&interval=4h&limit={limit}"
        )
        rows = _run(gate_url)
        candles = _to_candles(rows, "gateio")
        candles.sort(key=lambda c: c.timestamp)  # gate is ascending already; enforce anyway
        return candles, "gateio"

    def refresh(self, limit: int = 200, fill_history: bool = True, max_workers: int = 8) -> dict[str, Candle]:
        """并行拉取所有币的最新 K 线, 大幅缩短一轮耗时 (修复顺序拉取的连接不稳).

        fill_history=True 时全量刷新 history_cache; 否则仅增量合并最近 limit 根.
        """
        import time as _time

        def _fetch_one(coin: str) -> tuple[str, list[Candle], str] | None:
            try:
                candles, used = self._fetch(coin, limit=limit)
                candles.sort(key=lambda c: c.timestamp)
                return coin, candles, used
            except Exception as exc:
                return None

        latest: dict[str, Candle] = {}
        failures = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch_one, coin): coin for coin in self.coin_list}
            for fut in as_completed(futures):
                coin = futures[fut]
                result = fut.result()
                if result is None:
                    failures += 1
                    continue
                coin, candles, used = result
                if not candles:
                    continue
                self.last_error = None
                self.source = used
                # history_cache 只存已收线 bar (喂模型, 特征稳定); 未完成 bar 仅用于实时展示
                confirmed = [c for c in candles if c.confirmed]
                frame = pd.DataFrame(
                    {
                        "timestamp": [c.timestamp for c in confirmed],
                        "open": [c.open for c in confirmed],
                        "high": [c.high for c in confirmed],
                        "low": [c.low for c in confirmed],
                        "close": [c.close for c in confirmed],
                        "volume": [c.volume for c in confirmed],
                        "coin": [c.coin for c in confirmed],
                    }
                ) if confirmed else pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "coin"])
                if fill_history:
                    self.history_cache[coin] = frame
                elif coin in self.history_cache:
                    merged = self.history_cache[coin]
                    merged = merged[~merged["timestamp"].isin(frame["timestamp"])]
                    self.history_cache[coin] = pd.concat([merged, frame]).sort_values("timestamp").reset_index(drop=True).iloc[-200:]
                else:
                    self.history_cache[coin] = frame
                latest[coin] = candles[-1]  # 最新 bar (含未完成, 实时展示)
        if failures:
            self.last_error = f"{failures}/{len(self.coin_list)} coins failed this cycle"
        if not latest:
            raise OKXFeedError(self.last_error or "all coins failed")
        return latest

    def history(self, coin: str, n: int = 200) -> pd.DataFrame:
        if coin not in self.history_cache or self.history_cache[coin].empty:
            candles, _ = self._fetch(coin, limit=n)
            confirmed = [c for c in candles if c.confirmed]
            self.history_cache[coin] = pd.DataFrame(
                {
                    "timestamp": [c.timestamp for c in confirmed],
                    "open": [c.open for c in confirmed],
                    "high": [c.high for c in confirmed],
                    "low": [c.low for c in confirmed],
                    "close": [c.close for c in confirmed],
                    "volume": [c.volume for c in confirmed],
                    "coin": [c.coin for c in confirmed],
                }
            )
        frame = self.history_cache[coin]
        return frame.iloc[-n:].reset_index(drop=True)
