# AGENTS.md — OKX Quantitative Trading Gateway v2.00

## Quick Commands

```bash
# Start (tmux-persisted, survives shell close)
./start.sh start

# Stop / status / live logs
./start.sh stop
./start.sh status
./start.sh log          # attaches to tmux session (Ctrl+B, D to detach)

# Direct start (foreground)
python okx_quant_gateway.py

# Run all unit tests (unittest, NOT pytest)
python test_gateway.py

# Run persistent integration test
python persistent_test.py

# Install as systemd service
./start.sh install-service
```

## Architecture

**Entry point**: `okx_quant_gateway.py` — wires credential init, network isolation, state loading, Flask app + routes, then launches 4 background daemon threads.

**Background threads** (in `threads.py`):
| Thread | Interval | What it does |
|---|---|---|
| `data-updater` | 5 min | Fetches candles → computes indicators → caches in `state["indicator_cache"]` |
| `price-updater` | 30 s | Fetches live ticker prices → `state["price_cache"]` |
| `strategy-tick` | 30 s | Evaluates signals, opens/closes positions, circuit breaker, logs |
| `state-persistor` | 60 s | Persists dirty state to disk + MySQL (if configured) |

**State model**: Module-level mutable `state` dict in `state.py` — all components read/write it under `state_lock` (threading.Lock). Dirty flag uses `threading.Event` + `STATE_DIRTY_REF = [False]` (list used for pass-by-reference into `routes.py`).

**Web UI**: Flask on port 5000. The HTML template is inlined within `routes.py` (no separate frontend). All API routes are also in `routes.py`.

**Two operating modes**:
- `safe` — signals generated, email notifications sent, NO trading. Max positions = 50 (no limit).
- `running` — real/paper trading enabled. Max positions = 5. Circuit breaker after 5 consecutive losses.

## Critical Conventions

### Credentials — NEVER hardcode
- SMTP, DB, OKX API credentials go through `credential_manager.py` (encrypted local storage) OR `.env` file
- Copy `.env.example` → `.env` and edit. Required vars: `SMTP_SENDER`, `SMTP_AUTH_CODE`, `DB_PASSWORD`
- The app starts without credentials — use the web UI at `http://localhost:5000/settings` to configure post-launch
- Credential encryption: Fernet (primary) + XOR base64 (fallback for legacy). Machine-key derived.

### Thread safety
- All reads/writes to `state` dict MUST hold `state_lock`
- `STATE_DIRTY_REF[0] = True` to mark dirty (NOT `state_dirty = True`)
- New async code in threads: use `_run_async_loop()` pattern (creates fresh event loop per thread)

### Strategy parameters
- Stored in `data/strategy_params.json` (auto-created on first run)
- Regime-based: `bull` / `bear` / `range` parameters with separate SL/TP multipliers
- `constants.py` imports from `strategy_manager` — init must happen first
- GA-optimized V244 (Elite v2) parameters are the defaults

### Database
- **MySQL only** — no SQLite. Requires running MySQL server with `okx_gateway` database.
- Connection: `database.py` → `get_connection()` → `get_db_cursor()` context manager
- Tables: trading_pairs, open_positions, position_history, strategy_log, indicator_cache, price_cache, gateway_state
- Falls back gracefully — all DB operations return defaults if connection fails

### CSV candle storage
- Format: `data/{PAIR}_15m.csv` (dashes replaced with underscores)
- Thread-safe writes via `_csv_lock` in `storage.py`
- Deduplication by timestamp on append
- Temporary file + atomic rename pattern used

## Testing

- Framework: **unittest** (NOT pytest despite `.pytest_cache/` existing)
- Test file: `test_gateway.py` — fully isolated, uses `unittest.mock`, no real API calls
- `python test_gateway.py` — runs all test classes
- Test classes cover: indicators, signals, candle validation, CSV storage, notifications, circuit breaker logic, mocked API calls

## Environment Quirks

- **WSL/Windows**: Port 5000 on WSL isn't reachable from Windows host by default. Run `./start.sh port-forward` (admin) or `setup_wsl_portforward.bat` on Windows.
- `start.sh` hardcodes `PROJECT_DIR` — update if repo moves.
- `network_manager.py` creates a macvlan subnet for network isolation — fails gracefully if permissions missing.

## What NOT to do

- Don't swap MySQL for SQLite — the schema uses MySQL-specific types (DECIMAL, ENUM, JSON, ON DUPLICATE KEY UPDATE)
- Don't remove the `state_lock` acquisition from any state mutation
- Don't replace `unittest` with pytest in test_gateway.py without updating all imports
- Don't commit `.env` or `data/credentials.json` — both in `.gitignore`
- Don't remove `_prec()` rounding — it prevents floating-point display artifacts
