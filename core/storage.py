"""Storage utilities for OKX Quantitative Trading Gateway (CSV and state persistence)."""

import csv
import logging
import os
import threading

import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_DIR, "data")

# File lock for thread-safe CSV writes
_csv_lock = threading.Lock()


def _get_data_dir(module_data_dir=None):
    if module_data_dir:
        return module_data_dir
    import sys
    for name, mod in sys.modules.items():
        if name.startswith("okx_quant_gateway") and hasattr(mod, "DATA_DIR"):
            return mod.DATA_DIR
        if name == "gateway" and hasattr(mod, "DATA_DIR"):
            f = getattr(mod, "__file__", "")
            if "okx_quant_gateway" in f or not f:
                return mod.DATA_DIR
    return DATA_DIR


def pair_csv(pair, module_data_dir=None):
    safe = pair.replace("-", "_")
    return os.path.join(_get_data_dir(module_data_dir), f"{safe}_4H.csv")


def load_candles(pair, module_data_dir=None):
    path = pair_csv(pair, module_data_dir)
    if not os.path.exists(path):
        return []
    try:
        df = pd.read_csv(path)
        if df.empty:
            return []
        df = df.drop_duplicates(subset=["ts"])
        keep = (
            (df[["open", "high", "low", "close"]].abs() < 1e7).all(axis=1)
            & (df["high"] >= df["low"])
            & (df[["open", "high", "low", "close"]] > 0).all(axis=1)
        )
        df = df[keep]
        df = df.sort_values("ts")
        return df.to_dict("records")
    except Exception as e:
        logging.warning("load_candles failed for %s: %s", pair, e)
        return []


def append_candles(pair, candles, module_data_dir=None):
    if not candles:
        return
    with _csv_lock:
        try:
            path = pair_csv(pair, module_data_dir)
            file_exists = os.path.exists(path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            existing_ts = set()
            if file_exists:
                try:
                    with open(path, "r") as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            try:
                                existing_ts.add(int(row["ts"]))
                            except (ValueError, KeyError):
                                pass
                except Exception:
                    pass
            new_candles = [c for c in candles if c["ts"] not in existing_ts]
            if not new_candles:
                return
            tmp_path = path + ".tmp"
            with open(tmp_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["ts","open","high","low","close","vol"])
                if not file_exists:
                    writer.writeheader()
                else:
                    with open(path, "r") as orig:
                        f.write(orig.read())
                for c in new_candles:
                    writer.writerow({"ts": c["ts"], "open": c["open"], "high": c["high"],
                                     "low": c["low"], "close": c["close"], "vol": c["vol"]})
            os.replace(tmp_path, path)
        except Exception as e:
            logging.warning(f"append_candles failed for {pair}: {e}")

def cleanup_old_csvs(max_age_days=30, max_files=100):
    """Remove CSV files older than max_age_days or exceeding max_files limit."""
    import glob
    import time

    csv_dir = os.path.join(DATA_DIR)
    pattern = os.path.join(csv_dir, "*_4H.csv")
    files = glob.glob(pattern)

    if len(files) <= max_files:
        return 0

    files.sort(key=os.path.getmtime)
    removed = 0
    cutoff = time.time() - (max_age_days * 86400)

    for f in files[:-max_files]:
        try:
            if os.path.getmtime(f) < cutoff or len(files) - removed > max_files:
                os.remove(f)
                removed += 1
        except Exception:
            pass

    return removed


def warmup_cache_from_csvs(pairs, tail_rows=300):
    """Read tail of each pair's CSV and return {pair: [row_dict, ...]}."""
    import os
    result = {}
    for pair in pairs:
        path = pair_csv(pair)
        if not os.path.exists(path):
            continue
        try:
            df = pd.read_csv(path)
            if df.empty:
                continue
            df = df.drop_duplicates(subset=["ts"])
            df = df[df["high"] >= df["low"]]
            for col in ["open", "high", "low", "close"]:
                df = df[(df[col] > 0) & (df[col].abs() < 1e7)]
            df = df.sort_values("ts")
            rows = df.tail(tail_rows).to_dict("records")
            if rows:
                result[pair] = rows
        except Exception:
            pass
    return result
