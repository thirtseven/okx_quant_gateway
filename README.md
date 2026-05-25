# OKX Quantitative Trading Gateway v2.00

## 🔒 Security Improvements

### Critical Fixes (v1 → v2)
- **Removed all hardcoded credentials** - SMTP, database, and API credentials now loaded from environment variables
- **Added `.env.example` template** - Clear documentation for required configuration
- **Updated `.gitignore`** - Prevents accidental commit of sensitive files

### Configuration
```bash
# Copy and configure environment
cp .env.example .env
# Edit .env with your credentials

# Required environment variables:
SMTP_SENDER=your_email@example.com
SMTP_AUTH_CODE=your_smtp_auth_code
DB_HOST=127.0.0.1
DB_USER=okx_trader
DB_PASSWORD=your_secure_password
DB_NAME=okx_gateway
```

## 🛡️ Quality Improvements

### Thread Safety
- Replaced global `state_dirty` boolean with `threading.Event`
- Added `mark_dirty()`, `is_dirty()`, `get_state()`, `set_state()` helper functions
- Proper lock acquisition for all state mutations

### Async/Sync Handling
- Extracted `_run_async_loop()` helper to prevent resource leaks
- Consistent event loop management across threads
- Proper error handling in background threads

### API Security
- Added `flask-limiter` for rate limiting (200/min default, 10/min for mutations)
- Input validation for trading pairs (`validate_pair()`)
- Input validation for trading directions (`validate_direction()`)
- Proper HTTP status codes for error responses

### Database
- Removed hardcoded database credentials
- Added `get_db_cursor()` context manager
- Proper connection error handling

### Logging
- Replaced `print()` with `logging` module throughout
- Consistent log format and levels
- Better error context in log messages

## 📦 Dependencies

### Updated `requirements.txt`
- Locked dependency versions with `~=` (compatible release)
- Added `pymysql~=1.1.0` for database support
- Added `python-dotenv~=1.0.0` for .env file loading

### Docker Improvements
- Removed deprecated `FLASK_ENV` variable
- Added `env_file` support in docker-compose.yml
- Improved health check endpoint
- Added proper file permissions

## 🚀 Migration from v1

1. **Backup your data**
   ```bash
   cp -r /path/to/okx_b/data /path/to/backup/
   ```

2. **Configure environment**
   ```bash
   cd okx_b_v2
   cp .env.example .env
   # Edit .env with your credentials
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run tests**
   ```bash
   python test_gateway.py
   ```

5. **Start gateway**
   ```bash
   ./start.sh start
   ```

## 📋 Changes Summary

| Component | v1 Issue | v2 Fix |
|-----------|----------|--------|
| `constants.py` | Hardcoded SMTP credentials | Environment variables only |
| `database.py` | Hardcoded DB password | Environment variables + context manager |
| `state.py` | Thread-unsafe `state_dirty` | `threading.Event` + helper functions |
| `threads.py` | Event loop resource leaks | Extracted `_run_async_loop()` |
| `routes.py` | No rate limiting | `flask-limiter` with sensible defaults |
| `routes.py` | Weak input validation | `validate_pair()`, `validate_direction()` |
| `requirements.txt` | Unpinned versions | Locked with `~=` |
| `Dockerfile` | Deprecated env vars | Updated for Flask 3.x |
| `.gitignore` | Missing | Added to prevent credential leaks |

## 🔍 Testing

```bash
# Run unit tests
python test_gateway.py

# Run persistent tests
python persistent_test.py

# Check for security issues
grep -r "password\|secret\|api_key" *.py
```

## ⚠️ Breaking Changes

1. **Environment variables required** - v1 hardcoded credentials no longer work
2. **Database password** - Must set `DB_PASSWORD` environment variable
3. **SMTP credentials** - Must set `SMTP_SENDER` and `SMTP_AUTH_CODE`

## 📄 License

Same as v1 - see original project license.
