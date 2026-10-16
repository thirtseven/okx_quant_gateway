"""config.py — 网关运行时配置与持久化路径.

EXE 模式下所有运行时配置写入 EXE 所在目录 (而非临时解压目录),
保证重启后设置不丢失.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    BASE_DIR: Path = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

CONFIG_FILE: Path = BASE_DIR / "config.json"

DEFAULTS: dict = {
    "proxy_url": "http://127.0.0.1:7891",
    "okx_api": "https://www.okx.com/api/v5/market/candles",
    "gateway_port": 8321,
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save(payload: dict) -> dict:
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in payload.items() if k in DEFAULTS})
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return cfg