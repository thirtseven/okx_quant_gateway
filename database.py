"""MySQL database persistence for OKX Quantitative Trading Gateway."""

import json
import logging
import os
import time
from datetime import datetime
from typing import Optional, Dict, List, Any
from contextlib import contextmanager

import pymysql
from pymysql.cursors import DictCursor

logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("DB_PORT", "3306")),
    "user": os.environ.get("DB_USER", "okx_trader"),
    "password": os.environ.get("DB_PASSWORD"),
    "database": os.environ.get("DB_NAME", "okx_gateway"),
    "charset": "utf8mb4",
    "cursorclass": DictCursor,
    "autocommit": True,
}

if not DB_CONFIG["password"]:
    logger.warning("DB_PASSWORD not set - database operations will fail")

_connection: Optional[pymysql.Connection] = None


def get_connection() -> Optional[pymysql.Connection]:
    global _connection
    try:
        if _connection is None or not _connection.open:
            _connection = pymysql.connect(**DB_CONFIG)
            logger.info("[DB] Connected to MySQL")
        else:
            _connection.ping(reconnect=True)
        return _connection
    except Exception as e:
        logger.warning(f"[DB] MySQL connection failed: {e}")
        return None


@contextmanager
def get_db_cursor():
    conn = get_connection()
    if conn is None:
        raise RuntimeError("Database connection not available")
    cursor = conn.cursor()
    try:
        yield cursor
    finally:
        cursor.close()


def init_database():
    conn = get_connection()
    if conn is None:
        logger.warning("[DB] Cannot init database - no connection")
        return False
    
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trading_pairs (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    pair VARCHAR(50) NOT NULL UNIQUE,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT TRUE,
                    INDEX idx_pair (pair),
                    INDEX idx_active (is_active)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS open_positions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    pair VARCHAR(50) NOT NULL,
                    direction ENUM('LONG', 'SHORT') NOT NULL,
                    entry_price DECIMAL(20, 10) NOT NULL,
                    sl_price DECIMAL(20, 10) NOT NULL,
                    tp_price DECIMAL(20, 10) NOT NULL,
                    opened_at TIMESTAMP NOT NULL,
                    entry_bar BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uk_pair (pair),
                    INDEX idx_direction (direction),
                    INDEX idx_opened_at (opened_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS position_history (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    pair VARCHAR(50) NOT NULL,
                    direction ENUM('LONG', 'SHORT') NOT NULL,
                    entry_price DECIMAL(20, 10) NOT NULL,
                    exit_price DECIMAL(20, 10) NOT NULL,
                    exit_reason VARCHAR(20) NOT NULL,
                    pnl_pct DECIMAL(10, 4) NOT NULL,
                    is_win BOOLEAN NOT NULL,
                    opened_at TIMESTAMP NOT NULL,
                    closed_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_pair (pair),
                    INDEX idx_closed_at (closed_at),
                    INDEX idx_exit_reason (exit_reason)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS strategy_log (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    log_time TIMESTAMP NOT NULL,
                    signals_json JSON,
                    open_pos INT DEFAULT 0,
                    consecutive_losses INT DEFAULT 0,
                    circuit_broken BOOLEAN DEFAULT FALSE,
                    win_rate DECIMAL(5, 2) DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_log_time (log_time)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS indicator_cache (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    pair VARCHAR(50) NOT NULL,
                    signal_type VARCHAR(10),
                    norm_long DECIMAL(10, 6),
                    norm_short DECIMAL(10, 6),
                    rsi_14 DECIMAL(10, 4),
                    rsi_7 DECIMAL(10, 4),
                    macd_hist DECIMAL(20, 10),
                    bb_position DECIMAL(10, 6),
                    stoch_k DECIMAL(10, 4),
                    atr_14 DECIMAL(20, 10),
                    close_price DECIMAL(20, 10),
                    sl_price DECIMAL(20, 10),
                    tp_price DECIMAL(20, 10),
                    kline_ts BIGINT,
                    data_points INT DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uk_pair (pair),
                    INDEX idx_signal (signal_type),
                    INDEX idx_updated (updated_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS gateway_state (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    state_key VARCHAR(50) NOT NULL UNIQUE,
                    state_value JSON,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_key (state_key)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS price_cache (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    pair VARCHAR(50) NOT NULL,
                    last_price VARCHAR(30),
                    open24h VARCHAR(30),
                    vol24h VARCHAR(30),
                    ts BIGINT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uk_pair (pair),
                    INDEX idx_updated (updated_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
        
        logger.info("[DB] Database tables initialized successfully")
        return True
    except Exception as e:
        logger.error(f"[DB] Failed to init database: {e}")
        return False


def save_trading_pairs(pairs: List[str]):
    conn = get_connection()
    if conn is None:
        return False
    
    try:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE trading_pairs SET is_active = FALSE")
            for pair in pairs:
                cursor.execute(
                    "INSERT INTO trading_pairs (pair, is_active) VALUES (%s, TRUE) "
                    "ON DUPLICATE KEY UPDATE is_active = TRUE, added_at = added_at",
                    (pair,)
                )
        return True
    except Exception as e:
        logger.error(f"[DB] Failed to save trading pairs: {e}")
        return False


def load_trading_pairs() -> List[str]:
    conn = get_connection()
    if conn is None:
        return []
    
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT pair FROM trading_pairs WHERE is_active = TRUE ORDER BY pair")
            return [row["pair"] for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Failed to load trading pairs: {e}")
        return []


def save_open_positions(positions: List[Dict[str, Any]]):
    conn = get_connection()
    if conn is None:
        return False
    
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM open_positions")
            for pos in positions:
                cursor.execute(
                    "INSERT INTO open_positions (pair, direction, entry_price, sl_price, tp_price, opened_at, entry_bar) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (pos["pair"], pos["direction"], pos["entry"], pos["sl"], pos["tp"],
                     pos.get("opened_at"), pos.get("entry_bar"))
                )
        return True
    except Exception as e:
        logger.error(f"[DB] Failed to save open positions: {e}")
        return False


def load_open_positions() -> List[Dict[str, Any]]:
    conn = get_connection()
    if conn is None:
        return []
    
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM open_positions ORDER BY opened_at")
            positions = []
            for row in cursor.fetchall():
                positions.append({
                    "pair": row["pair"],
                    "direction": row["direction"],
                    "entry": float(row["entry_price"]),
                    "sl": float(row["sl_price"]),
                    "tp": float(row["tp_price"]),
                    "opened_at": str(row["opened_at"]),
                    "entry_bar": row["entry_bar"],
                })
            return positions
    except Exception as e:
        logger.error(f"[DB] Failed to load open positions: {e}")
        return []


def save_position_history(history: List[Dict[str, Any]]):
    conn = get_connection()
    if conn is None:
        return False
    
    try:
        with conn.cursor() as cursor:
            for h in history:
                cursor.execute(
                    "INSERT IGNORE INTO position_history "
                    "(pair, direction, entry_price, exit_price, exit_reason, pnl_pct, is_win, opened_at, closed_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (h["pair"], h["direction"], h["entry"], h["exit"],
                     h["exit_reason"], h["pnl"], h["win"],
                     h.get("opened_at", "2020-01-01 00:00:00"), h["closed_at"])
                )
        return True
    except Exception as e:
        logger.error(f"[DB] Failed to save position history: {e}")
        return False


def load_position_history(limit: int = 200) -> List[Dict[str, Any]]:
    conn = get_connection()
    if conn is None:
        return []
    
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM position_history ORDER BY closed_at DESC LIMIT %s",
                (limit,)
            )
            history = []
            for row in cursor.fetchall():
                history.append({
                    "pair": row["pair"],
                    "direction": row["direction"],
                    "entry": float(row["entry_price"]),
                    "exit": float(row["exit_price"]),
                    "exit_reason": row["exit_reason"],
                    "pnl": float(row["pnl_pct"]),
                    "win": bool(row["is_win"]),
                    "closed_at": str(row["closed_at"]),
                })
            return history
    except Exception as e:
        logger.error(f"[DB] Failed to load position history: {e}")
        return []


def save_strategy_log(entry: Dict[str, Any]):
    conn = get_connection()
    if conn is None:
        return False
    
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO strategy_log (log_time, signals_json, open_pos, consecutive_losses, circuit_broken, win_rate) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (entry["time"], json.dumps(entry["signals"]), entry.get("open_pos", 0),
                 entry.get("consecutive_losses", 0), entry.get("circuit_broken", False),
                 entry.get("win_rate", 0))
            )
        return True
    except Exception as e:
        logger.error(f"[DB] Failed to save strategy log: {e}")
        return False


def load_strategy_log(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection()
    if conn is None:
        return []
    
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM strategy_log ORDER BY log_time DESC LIMIT %s",
                (limit,)
            )
            logs = []
            for row in cursor.fetchall():
                logs.append({
                    "time": str(row["log_time"]),
                    "signals": json.loads(row["signals_json"]) if row["signals_json"] else [],
                    "open_pos": row["open_pos"],
                    "consecutive_losses": row["consecutive_losses"],
                    "circuit_broken": bool(row["circuit_broken"]),
                    "win_rate": float(row["win_rate"]),
                })
            return logs
    except Exception as e:
        logger.error(f"[DB] Failed to load strategy log: {e}")
        return []


def save_indicator_cache(cache: Dict[str, Dict[str, Any]]):
    conn = get_connection()
    if conn is None:
        return False
    
    try:
        with conn.cursor() as cursor:
            for pair, data in cache.items():
                cursor.execute(
                    "INSERT INTO indicator_cache "
                    "(pair, signal_type, norm_long, norm_short, rsi_14, rsi_7, macd_hist, "
                    "bb_position, stoch_k, atr_14, close_price, sl_price, tp_price, kline_ts, data_points) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE "
                    "signal_type=VALUES(signal_type), norm_long=VALUES(norm_long), norm_short=VALUES(norm_short), "
                    "rsi_14=VALUES(rsi_14), rsi_7=VALUES(rsi_7), macd_hist=VALUES(macd_hist), "
                    "bb_position=VALUES(bb_position), stoch_k=VALUES(stoch_k), atr_14=VALUES(atr_14), "
                    "close_price=VALUES(close_price), sl_price=VALUES(sl_price), tp_price=VALUES(tp_price), "
                    "kline_ts=VALUES(kline_ts), data_points=VALUES(data_points)",
                    (pair, data.get("signal"), data.get("norm_long"), data.get("norm_short"),
                     data.get("rsi_14"), data.get("rsi_7"), data.get("macd_hist"),
                     data.get("bb_position"), data.get("stoch_k"), data.get("atr_14"),
                     data.get("close"), data.get("sl"), data.get("tp"),
                     data.get("kline_ts"), data.get("data_points", 0))
                )
        return True
    except Exception as e:
        logger.error(f"[DB] Failed to save indicator cache: {e}")
        return False


def load_indicator_cache() -> Dict[str, Dict[str, Any]]:
    conn = get_connection()
    if conn is None:
        return {}
    
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM indicator_cache")
            cache = {}
            for row in cursor.fetchall():
                cache[row["pair"]] = {
                    "pair": row["pair"],
                    "signal": row["signal_type"],
                    "norm_long": float(row["norm_long"]) if row["norm_long"] else 0,
                    "norm_short": float(row["norm_short"]) if row["norm_short"] else 0,
                    "rsi_14": float(row["rsi_14"]) if row["rsi_14"] else 0,
                    "rsi_7": float(row["rsi_7"]) if row["rsi_7"] else 0,
                    "macd_hist": float(row["macd_hist"]) if row["macd_hist"] else 0,
                    "bb_position": float(row["bb_position"]) if row["bb_position"] else 0,
                    "stoch_k": float(row["stoch_k"]) if row["stoch_k"] else 0,
                    "atr_14": float(row["atr_14"]) if row["atr_14"] else 0,
                    "close": float(row["close_price"]) if row["close_price"] else 0,
                    "sl": float(row["sl_price"]) if row["sl_price"] else 0,
                    "tp": float(row["tp_price"]) if row["tp_price"] else 0,
                    "kline_ts": row["kline_ts"],
                    "data_points": row["data_points"],
                }
            return cache
    except Exception as e:
        logger.error(f"[DB] Failed to load indicator cache: {e}")
        return {}


def save_price_cache(prices: Dict[str, Dict[str, Any]]):
    conn = get_connection()
    if conn is None:
        return False
    
    try:
        with conn.cursor() as cursor:
            for pair, ticker in prices.items():
                cursor.execute(
                    "INSERT INTO price_cache (pair, last_price, open24h, vol24h, ts) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE "
                    "last_price=VALUES(last_price), open24h=VALUES(open24h), "
                    "vol24h=VALUES(vol24h), ts=VALUES(ts)",
                    (pair, ticker.get("last"), ticker.get("open24h"),
                     ticker.get("vol24h"), ticker.get("ts"))
                )
        return True
    except Exception as e:
        logger.error(f"[DB] Failed to save price cache: {e}")
        return False


def load_price_cache() -> Dict[str, Dict[str, Any]]:
    conn = get_connection()
    if conn is None:
        return {}
    
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM price_cache")
            prices = {}
            for row in cursor.fetchall():
                prices[row["pair"]] = {
                    "last": row["last_price"],
                    "open24h": row["open24h"],
                    "vol24h": row["vol24h"],
                    "ts": row["ts"],
                }
            return prices
    except Exception as e:
        logger.error(f"[DB] Failed to load price cache: {e}")
        return {}


def save_gateway_state(key: str, value: Any):
    conn = get_connection()
    if conn is None:
        return False
    
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO gateway_state (state_key, state_value) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE state_value=VALUES(state_value)",
                (key, json.dumps(value))
            )
        return True
    except Exception as e:
        logger.error(f"[DB] Failed to save gateway state {key}: {e}")
        return False


def load_gateway_state(key: str, default: Any = None) -> Any:
    conn = get_connection()
    if conn is None:
        return default
    
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT state_value FROM gateway_state WHERE state_key = %s", (key,))
            row = cursor.fetchone()
            if row and row["state_value"]:
                return json.loads(row["state_value"])
            return default
    except Exception as e:
        logger.error(f"[DB] Failed to load gateway state {key}: {e}")
        return default


def save_full_state(state: Dict[str, Any]):
    save_trading_pairs(state.get("trading_pairs", []))
    save_open_positions(state.get("open_positions", []))
    save_position_history(state.get("position_history", []))
    
    for entry in state.get("strategy_log", [])[-50:]:
        save_strategy_log(entry)
    
    save_gateway_state("consecutive_losses", state.get("consecutive_losses", 0))
    save_gateway_state("circuit_broken", state.get("circuit_broken", False))
    save_gateway_state("circuit_broken_until", state.get("circuit_broken_until"))
    save_gateway_state("strategy_active", state.get("strategy_active", False))
    save_gateway_state("mode", state.get("mode", "safe"))


def load_full_state() -> Dict[str, Any]:
    return {
        "trading_pairs": load_trading_pairs(),
        "open_positions": load_open_positions(),
        "position_history": load_position_history(),
        "strategy_log": load_strategy_log(),
        "consecutive_losses": load_gateway_state("consecutive_losses", 0),
        "circuit_broken": load_gateway_state("circuit_broken", False),
        "circuit_broken_until": load_gateway_state("circuit_broken_until"),
        "strategy_active": load_gateway_state("strategy_active", False),
        "mode": load_gateway_state("mode", "safe"),
    }


def is_available() -> bool:
    return get_connection() is not None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Testing MySQL database module...")
    
    if init_database():
        print("✅ Database initialized")
        
        test_pairs = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]
        if save_trading_pairs(test_pairs):
            loaded = load_trading_pairs()
            print(f"✅ Trading pairs: {loaded}")
        
        test_pos = [{
            "pair": "BTC-USDT-SWAP",
            "direction": "LONG",
            "entry": 80000.0,
            "sl": 78000.0,
            "tp": 83000.0,
            "opened_at": "2026-05-08 12:00:00",
            "entry_bar": 1234567890,
        }]
        if save_open_positions(test_pos):
            loaded = load_open_positions()
            print(f"✅ Open positions: {loaded}")
        
        print("✅ All tests passed!")
    else:
        print("❌ Database initialization failed")
