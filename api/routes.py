"""Flask routes and HTML template for OKX Quantitative Gateway."""
import asyncio
import json
import logging
import os
import re
import secrets
import threading
import time
import arrow
from flask import Flask, render_template_string, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from core.constants import OKX_BASE, DEFAULT_INSTRUMENTS, VERSION, PROXY, PROXIES, build_genome
from auth.credentials import _simple_encrypt, _simple_decrypt

logger = logging.getLogger(__name__)

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per minute", "50 per second"],
    storage_uri="memory://",
)


def validate_pair(pair: str) -> bool:
    if not pair or not isinstance(pair, str):
        return False
    pattern = r'^[A-Z0-9]+-[A-Z]+-(SWAP|FUTURES|SPOT)$'
    return bool(re.match(pattern, pair.upper()))


def validate_direction(direction: str) -> bool:
    return direction in ("LONG", "SHORT", "HOLD")

def report_success(proxy=None):
    pass

def report_failure(proxy=None):
    pass

MAX_POSITIONS = 999999  # No limit — overtrading controlled via cooldown penalty

TEMPLATE = ""  # HTML moved to static/index.html


SETTINGS_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OKX 量化网关 - 设置</title>
<style>
:root{--bg:#0f1210;--bg2:#161c18;--bg3:#1d2620;--border:#2a3830;--accent:#4ade80;--text:#d4e8da;--text2:#9ab8a2;--red:#f87171;--mono:'Consolas',monospace;--sans:'Segoe UI',sans-serif}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:14px;min-height:100vh;padding:20px}
.container{max-width:1200px;margin:0 auto}
h1{font-family:var(--mono);color:var(--accent);margin-bottom:20px}
h2{font-family:var(--mono);color:var(--accent);margin:20px 0 10px;font-size:16px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:20px}
.form-group{margin-bottom:15px}
.form-group label{display:block;margin-bottom:5px;color:var(--text2);font-size:12px}
.form-group input,.form-group select,.form-group textarea{width:100%;padding:8px 12px;background:var(--bg3);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)}
.form-group input:focus,.form-group select:focus,.form-group textarea:focus{outline:none;border-color:var(--accent)}
.btn{padding:8px 16px;border:none;border-radius:4px;cursor:pointer;font-family:var(--mono);font-size:12px}
.btn-primary{background:var(--accent);color:var(--bg)}
.btn-secondary{background:var(--bg3);color:var(--text);border:1px solid var(--border)}
.btn-danger{background:var(--red);color:white}
.btn:hover{opacity:0.9}
.row{display:flex;gap:15px}
.col{flex:1}
.status{padding:10px;border-radius:4px;margin-top:10px;font-family:var(--mono);font-size:12px}
.status.success{background:#1a3a2a;border:1px solid #2a5a3a}
.status.error{background:#3a1a1a;border:1px solid #5a2a2a}
.nav{display:flex;gap:10px;margin-bottom:20px}
.nav a{padding:8px 16px;background:var(--bg2);border:1px solid var(--border);border-radius:4px;color:var(--text2);text-decoration:none;font-family:var(--mono);font-size:12px}
.nav a:hover,.nav a.active{background:var(--bg3);color:var(--accent);border-color:var(--accent)}
</style>
</head>
<body>
<div class="container">
<h1>OKX 量化网关设置</h1>

<div class="nav">
    <a href="/">仪表盘</a>
    <a href="/settings" class="active">设置</a>
</div>

<div class="card">
<h2>SMTP 邮件配置 (163邮箱)</h2>
<div class="row">
<div class="col">
<div class="form-group">
    <label>SMTP 发件人</label>
    <input type="email" id="smtp_sender" placeholder="your_email@163.com">
</div>
</div>
<div class="col">
<div class="form-group">
    <label>SMTP 授权码</label>
    <input type="password" id="smtp_auth_code" placeholder="163邮箱授权码 (非登录密码)">
</div>
</div>
</div>
<div class="row">
<div class="col">
<div class="form-group">
    <label>SMTP 服务器</label>
    <input type="text" id="smtp_host" placeholder="smtp.163.com" value="smtp.163.com">
</div>
</div>
<div class="col">
<div class="form-group">
    <label>SMTP 端口</label>
    <input type="number" id="smtp_port" placeholder="465" value="465">
</div>
</div>
</div>
<button class="btn btn-primary" onclick="saveSMTP()">保存 SMTP</button>
<button class="btn btn-secondary" onclick="testSMTP()">测试发送</button>
<div id="smtp_status"></div>
</div>

<div class="card">
<h2>数据库配置</h2>
<div class="row">
<div class="col">
<div class="form-group">
    <label>主机</label>
    <input type="text" id="db_host" placeholder="127.0.0.1">
</div>
</div>
<div class="col">
<div class="form-group">
    <label>端口</label>
    <input type="number" id="db_port" placeholder="3306">
</div>
</div>
<div class="col">
<div class="form-group">
    <label>用户名</label>
    <input type="text" id="db_user" placeholder="okx_trader">
</div>
</div>
</div>
<div class="row">
<div class="col">
<div class="form-group">
    <label>密码</label>
    <input type="password" id="db_password" placeholder="密码">
</div>
</div>
<div class="col">
<div class="form-group">
    <label>数据库名</label>
    <input type="text" id="db_name" placeholder="okx_gateway">
</div>
</div>
</div>
<button class="btn btn-primary" onclick="saveDB()">保存数据库</button>
<div id="db_status"></div>
</div>

<div class="card">
<h2>OKX API 配置</h2>
<div class="row">
<div class="col">
<div class="form-group">
    <label>API Key</label>
    <input type="text" id="okx_api_key" placeholder="API Key">
</div>
</div>
<div class="col">
<div class="form-group">
    <label>Secret Key</label>
    <input type="password" id="okx_secret_key" placeholder="Secret Key">
</div>
</div>
<div class="col">
<div class="form-group">
    <label>Passphrase</label>
    <input type="password" id="okx_passphrase" placeholder="Passphrase">
</div>
</div>
</div>
<button class="btn btn-primary" onclick="saveOKX()">保存 OKX</button>
<div id="okx_status"></div>
</div>

<div class="card">
<h2>API 访问密钥</h2>
<div class="form-group">
    <label>API Key (留空则不启用认证)</label>
    <input type="text" id="api_key" placeholder="设置后所有 API 请求需携带 X-API-Key header">
</div>
<button class="btn btn-primary" onclick="saveAPIKey()">保存 API Key</button>
<div id="apikey_status"></div>
</div>

<div class="card">
<h2>策略模式</h2>
<div class="row">
<div class="col">
<div class="form-group">
    <label>市场模式</label>
    <select id="regime_mode">
        <option value="auto">自动检测</option>
        <option value="bull">牛市 (Bull)</option>
        <option value="bear">熊市 (Bear)</option>
        <option value="range">震荡 (Range)</option>
    </select>
</div>
</div>
<div class="col">
<div class="form-group">
    <label>当前生效参数</label>
    <div id="active_regime_display" style="padding:8px 12px;background:var(--bg3);border:1px solid var(--border);border-radius:4px;font-family:var(--mono);font-size:12px;color:var(--accent)">--</div>
</div>
</div>
</div>
<button class="btn btn-primary" onclick="saveRegimeMode()">切换模式</button>
<div id="regime_status"></div>
</div>

<div class="card">
<h2>策略参数</h2>
<div class="row">
<div class="col">
<div class="form-group">
    <label>趋势阈值 (%)</label>
    <input type="number" id="trend_threshold" step="0.01" placeholder="2.0">
</div>
</div>
<div class="col">
<div class="form-group">
    <label>得分阈值</label>
    <input type="number" id="score_min" step="0.01" placeholder="0.55">
</div>
</div>
<div class="col">
<div class="form-group">
    <label>ATR 止损倍数</label>
    <input type="number" id="atr_sl" step="0.01" placeholder="3.99">
</div>
</div>
<div class="col">
<div class="form-group">
    <label>ATR 止盈倍数</label>
    <input type="number" id="atr_tp" step="0.01" placeholder="3.61">
</div>
</div>
</div>
<div class="row">
<div class="col">
<div class="form-group">
    <label>最大持仓 K 线数</label>
    <input type="number" id="max_bars" placeholder="22">
</div>
</div>
<div class="col">
<div class="form-group">
    <label>Kelly 仓位比例</label>
    <input type="number" id="kelly_fraction" step="0.01" placeholder="0.2">
</div>
</div>
<div class="col">
<div class="form-group">
    <label>冷却期 K 线数</label>
    <input type="number" id="cooldown_bars" placeholder="30">
</div>
</div>
</div>
<button class="btn btn-primary" onclick="saveStrategy()">保存策略</button>
<button class="btn btn-secondary" onclick="resetStrategy()">重置默认</button>
<button class="btn btn-secondary" onclick="loadStrategy()">重新加载</button>
<div id="strategy_status"></div>
</div>

<div class="card">
<h2>配置管理</h2>
<button class="btn btn-secondary" onclick="exportConfig()">导出配置</button>
<button class="btn btn-secondary" onclick="importConfig()">导入配置</button>
<button class="btn btn-danger" onclick="if(confirm('确认重置所有配置?'))resetAll()">重置所有</button>
<div id="config_status"></div>
</div>

</div>

 <script>
const API = '';
const CSRF_TOKEN = "{{ csrf_token }}";

async function api(path, method='GET', body=null) {
    const sep = path.includes('?') ? '&' : '?';
    const url = API + path + sep + 'csrf_token=' + encodeURIComponent(CSRF_TOKEN);
    const opts = {method, headers:{'Content-Type':'application/json'}};
    if(body) opts.body = JSON.stringify(body);
    opts.headers['X-CSRF-Token'] = CSRF_TOKEN;
    const r = await fetch(url, opts);
    return r.json();
}

function showStatus(id, msg, ok) {
    const el = document.getElementById(id);
    el.className = 'status ' + (ok ? 'success' : 'error');
    el.textContent = msg;
    setTimeout(() => el.textContent = '', 3000);
}

async function loadCredentials() {
    const r = await api('/api/credentials/values');
    if(r.smtp_sender) document.getElementById('smtp_sender').value = r.smtp_sender;
    if(r.smtp_auth_code) document.getElementById('smtp_auth_code').value = r.smtp_auth_code;
    if(r.smtp_host) document.getElementById('smtp_host').value = r.smtp_host;
    if(r.smtp_port) document.getElementById('smtp_port').value = r.smtp_port;
    if(r.db_host) document.getElementById('db_host').value = r.db_host;
    if(r.db_port) document.getElementById('db_port').value = r.db_port;
    if(r.db_user) document.getElementById('db_user').value = r.db_user;
    if(r.db_name) document.getElementById('db_name').value = r.db_name;
}

async function loadStrategy() {
    const r = await api('/api/strategy/params');
    if(r.trend) document.getElementById('trend_threshold').value = r.trend.slope_threshold * 100;
    if(r.entry) document.getElementById('score_min').value = r.entry.score_min;
    if(r.exit) {
        document.getElementById('atr_sl').value = r.exit.atr_sl;
        document.getElementById('atr_tp').value = r.exit.atr_tp;
        document.getElementById('max_bars').value = r.exit.max_bars;
    }
    if(r.confirmation) {
        document.getElementById('kelly_fraction').value = r.confirmation.kelly_fraction;
        document.getElementById('cooldown_bars').value = r.confirmation.cooldown_bars;
    }
}

async function saveSMTP() {
    const sender = document.getElementById('smtp_sender').value;
    const code = document.getElementById('smtp_auth_code').value;
    const host = document.getElementById('smtp_host').value;
    const port = document.getElementById('smtp_port').value;
    if(sender) await api('/api/credentials', 'POST', {type:'smtp_sender', value:sender});
    if(code) await api('/api/credentials', 'POST', {type:'smtp_auth_code', value:code});
    if(host) await api('/api/credentials', 'POST', {type:'smtp_host', value:host});
    if(port) await api('/api/credentials', 'POST', {type:'smtp_port', value:port});
    showStatus('smtp_status', 'SMTP 已保存', true);
}

async function testSMTP() {
    const r = await api('/api/test-email', 'POST');
    showStatus('smtp_status', r.ok ? '测试邮件已发送' : '发送失败: ' + r.msg, r.ok);
}

async function saveDB() {
    const fields = ['db_host','db_port','db_user','db_password','db_name'];
    for(const f of fields) {
        const v = document.getElementById(f).value;
        if(v) await api('/api/credentials', 'POST', {type:f, value:v});
    }
    showStatus('db_status', '数据库配置已保存', true);
}

async function saveOKX() {
    const fields = ['okx_api_key','okx_secret_key','okx_passphrase'];
    for(const f of fields) {
        const v = document.getElementById(f).value;
        if(v) await api('/api/credentials', 'POST', {type:f, value:v});
    }
    showStatus('okx_status', 'OKX API 已保存', true);
}

async function saveAPIKey() {
    const v = document.getElementById('api_key').value;
    await api('/api/credentials', 'POST', {type:'api_key', value:v});
    showStatus('apikey_status', v ? 'API Key 已保存' : 'API Key 已清除 (认证已禁用)', true);
}

async function saveStrategy() {
    const params = {
        trend: {slope_threshold: parseFloat(document.getElementById('trend_threshold').value) / 100},
        entry: {score_min: parseFloat(document.getElementById('score_min').value)},
        exit: {
            atr_sl: parseFloat(document.getElementById('atr_sl').value),
            atr_tp: parseFloat(document.getElementById('atr_tp').value),
            max_bars: parseInt(document.getElementById('max_bars').value)
        },
        confirmation: {
            kelly_fraction: parseFloat(document.getElementById('kelly_fraction').value),
            cooldown_bars: parseInt(document.getElementById('cooldown_bars').value)
        }
    };
    const r = await api('/api/strategy/params', 'PUT', params);
    showStatus('strategy_status', r.ok ? '策略已保存' : '保存失败', r.ok);
}

async function resetStrategy() {
    const r = await api('/api/strategy/params/reset', 'POST');
    if(r.ok) loadStrategy();
    showStatus('strategy_status', r.ok ? '已重置为默认值' : '重置失败', r.ok);
}

async function exportConfig() {
    const r = await api('/api/config/export');
    const blob = new Blob([JSON.stringify(r, null, 2)], {type:'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'okx_gateway_config.json';
    a.click();
    showStatus('config_status', '配置已导出', true);
}

async function importConfig() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = async (e) => {
        const file = e.target.files[0];
        const text = await file.text();
        const data = JSON.parse(text);
        const r = await api('/api/config/import', 'POST', data);
        showStatus('config_status', r.ok ? '配置已导入' : '导入失败', r.ok);
        if(r.ok) { loadCredentials(); loadStrategy(); }
    };
    input.click();
}

async function resetAll() {
    await api('/api/strategy/params/reset', 'POST');
    loadStrategy();
    showStatus('config_status', '已重置所有配置', true);
}

loadCredentials();
loadStrategy();
loadRegime();

async function loadRegime() {
    const r = await api('/api/strategy/regime');
    if(r.mode) {
        document.getElementById('regime_mode').value = r.mode;
        const labels = {auto:'自动检测',bull:'牛市 (Bull)',bear:'熊市 (Bear)',range:'震荡 (Range)'};
        document.getElementById('active_regime_display').textContent = labels[r.mode] || r.mode;
    }
}

async function saveRegimeMode() {
    const mode = document.getElementById('regime_mode').value;
    const r = await api('/api/strategy/regime', 'PUT', {mode: mode});
    showStatus('regime_status', r.ok ? '模式已切换: ' + mode : '切换失败', r.ok);
    if(r.ok) loadRegime();
}
</script>
</body>
</html>
"""


def fetch_all_usdt_swap_instruments():
    from okx_client import fetch_all_usdt_swap_instruments as _fetch
    return _fetch()


def register_routes(app, state_ref, state_lock_ref, compute_signal_ref, notify_signal_ref,
                    fetch_pair_candles_ref, calculate_for_pair_ref, fetch_all_prices_ref,
                    save_state_ref, state_dirty_ref, MAX_POSITIONS):
    local_state = state_ref
    local_lock = state_lock_ref
    compute_signal = compute_signal_ref
    notify_signal = notify_signal_ref
    fetch_pair_candles = fetch_pair_candles_ref
    calculate_for_pair = calculate_for_pair_ref
    fetch_all_prices = fetch_all_prices_ref
    save_state = save_state_ref
    state_dirty = state_dirty_ref

    def current_genome():
        """Return fresh genome reflecting current strategy params."""
        return build_genome()

    AUTH_EXEMPT = {"/api/health", "/gateway-ui", "/api/prices", "/", "/favicon.ico"}

    @app.errorhandler(500)
    @app.errorhandler(404)
    @app.errorhandler(405)
    def _handle_api_error(e):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "msg": str(e)}), e.code if hasattr(e, "code") else 500
        return e

    @app.before_request
    def _check_api_key():
        from flask import request as _req
        if _req.path in AUTH_EXEMPT:
            return
        if _req.method == "OPTIONS":
            return
        from auth.credentials import get_credential
        expected = get_credential("api_key")
        if not expected:
            return
        provided = _req.headers.get("X-API-Key") or _req.args.get("key")
        if provided != expected:
            from flask import jsonify as _jsonify
            return _jsonify({"ok": False, "msg": "Unauthorized: invalid or missing API key"}), 401

    @app.after_request
    def _add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.route("/favicon.ico")
    def favicon():
        import os as _os
        from flask import send_from_directory
        favicon_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "static")
        return send_from_directory(favicon_path, "favicon.svg", mimetype="image/svg+xml")

    @app.route("/gateway-ui")
    def gateway_ui():
        import os
        from flask import send_from_directory
        from werkzeug.utils import safe_join
        ui_dir = os.path.dirname(os.path.abspath(__file__))
        ui_file = safe_join(ui_dir, "gateway-ui.html")
        if ui_file is None or not os.path.isfile(ui_file):
            return "gateway-ui.html not found", 404
        return send_from_directory(ui_dir, "gateway-ui.html")

    @app.route("/")
    def index():
        import os as _os
        project_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        html_path = _os.path.join(project_root, "static", "index.html")
        with open(html_path, "r", encoding="utf-8") as _f:
            html = _f.read()
        html = html.replace("{{ csrf_token }}", "local-gateway-no-csrf")
        html = html.replace("{{ version }}", VERSION)
        return html

    @app.route("/api/state")
    def api_state():
        return jsonify({k: v for k, v in local_state.items() if k not in ["running_accounts"]})

    @app.route("/api/prices")
    def api_prices():
        return jsonify({"prices": local_state["price_cache"], "price_updated": local_state.get("price_updated", "never"), "price_fetch_ms": local_state.get("price_timing", {}).get("fetch_ms", 0), "error": local_state.get("last_error")})

    @app.route("/api/prices/refresh", methods=["POST"])
    def api_prices_refresh():
        import asyncio
        def do_refresh():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                fetch_start = time.time()
                prices = loop.run_until_complete(fetch_all_prices(local_state, local_lock))
                fetch_ms = round((time.time() - fetch_start) * 1000)
                local_state["price_cache"].update(prices)
                local_state["last_error"] = None
                local_state["price_updated"] = arrow.now().format("HH:mm:ss")
                local_state["price_fetch_ms"] = fetch_ms
                logger.info("[PRICE_REFRESH] %s refreshed %d prices", arrow.now().format("HH:mm:ss"), len(prices))
            except Exception as e:
                local_state["last_error"] = f"Price refresh failed: {e}"
                logger.error("[PRICE_REFRESH] ERROR: %s", e)
            finally:
                loop.close()
        threading.Thread(target=do_refresh, daemon=True).start()
        return jsonify({"ok": True, "msg": "Price refresh started in background"})

    @app.route("/api/indicators")
    def api_indicators():
        timing = local_state.get("last_timing", {})
        last_update = local_state.get("last_update", "never")
        refreshing = local_state.get("indicators_refreshing", False)
        pair_count = len(local_state.get("indicator_cache", {}))
        return jsonify({"pair_count": pair_count, "last_update": last_update,
                        "refreshing": refreshing, "timing": timing,
                        "indicators": {}, "interval_seconds": 7200})

    @app.route("/api/signals")
    def api_signals():
        from indicators.lgbm import get_params, is_loaded as lgbm_loaded
        g = build_genome()
        cached = dict(local_state.get("indicator_cache", {}))
        signals = {}
        for pair, result in cached.items():
            try:
                if not (result and result.get("close")):
                    continue
                e25 = result.get("ema_25", 0)
                e50 = result.get("ema_50", 0)
                regime = "bull" if e25 > e50 * 1.02 else ("bear" if e25 < e50 * 0.98 else "range")
                signals[pair] = {
                    "signal": result.get("signal", "HOLD"), "regime": regime,
                    "norm_long": result.get("score", result.get("norm_long", 0)),
                    "norm_short": result.get("norm_short", 0),
                    "rsi_14": result.get("rsi_14"), "close": result.get("close"),
                }
            except Exception:
                pass
        thresholds = {"regime_mode": g.get("regime_mode", "auto"),
                      "regimes": {r: dict(p.items()) for r, p in g["regimes"].items()}}
        return jsonify({"signals": signals, "thresholds": thresholds, "computing": False})

    @app.route("/api/strategy/model")
    def api_strategy_model():
        from indicators.lgbm import get_params, is_loaded, _load_model
        _load_model()
        if not is_loaded():
            return jsonify({"loaded": False})
        p = get_params()
        features = p.get("features", {})
        if isinstance(features, dict):
            n_features = features.get("selected", 0)
            feature_names = features.get("names", [])
        else:
            n_features = len(features) if isinstance(features, list) else 0
            feature_names = features if isinstance(features, list) else []
        
        test_metrics = p.get("test", {})
        
        return jsonify({
            "loaded": True,
            "version": p.get("version", "v315"),
            "champion": p.get("champion", "GA"),
            "model": {
                "max_depth": p.get("max_depth", 2),
                "n_estimators": p.get("n_estimators", 100),
                "num_leaves": p.get("num_leaves", 4),
            },
            "trade": {
                "max_hold_bars": p.get("max_hold_bars", 12),
                "position": "long_only",
            },
            "n_features": n_features,
            "feature_names": feature_names,
            "test_wr": test_metrics.get("wr", 0),
            "test_pf": test_metrics.get("pf", 0),
            "test_corr": test_metrics.get("corr", 0),
            "test_trades": test_metrics.get("trades", 0),
            "fitness": p.get("best_fitness", 0),
        })

    @app.route("/api/threshold", methods=["PATCH"])
    def api_threshold():
        d = request.get_json()
        th = d.get("score_threshold")
        if th is None or not (0.1 <= th <= 1.0):
            return jsonify({"ok": False, "msg": "score_threshold must be between 0.1 and 1.0"})
        from strategy.manager import set_param
        for regime in ("bull", "bear", "range"):
            set_param(f"regimes.{regime}.score_min", float(th))
        state_dirty[0] = True
        save_state()
        return jsonify({"ok": True, "score_threshold": float(th)})

    @app.route("/api/positions")
    def api_positions():
        history = local_state.get("position_history", [])
        wins = sum(1 for h in history if h.get("win", False))
        total = len(history)
        recent_pnl = [h["pnl"] for h in history[-20:]]
        total_pnl = sum(h["pnl"] for h in history)
        return jsonify({
            "open_positions": local_state.get("open_positions", []),
            "open_count": len(local_state.get("open_positions", [])),
            "max_positions": 50 if local_state.get("mode") == "safe" else MAX_POSITIONS,
            "history": history[-50:],
            "total_closed": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "recent_pnl": recent_pnl,
            "total_pnl": round(total_pnl, 2),
            "consecutive_losses": local_state.get("consecutive_losses", 0),
            "circuit_broken": local_state.get("circuit_broken", False),
            "circuit_broken_until": local_state.get("circuit_broken_until"),
            "pending_signals": local_state.get("pending_signals", []),
            "strategy": current_genome(),
        })

    @app.route("/api/positions/reset", methods=["POST"])
    def api_positions_reset():
        with local_lock:
            local_state["open_positions"] = []
            local_state["position_history"] = []
            local_state["consecutive_losses"] = 0
            local_state["circuit_broken"] = False
            local_state["circuit_broken_until"] = None
            local_state["pending_signals"] = []
            local_state["strategy_log"] = []
        state_dirty[0] = True
        save_state()
        import database
        database.save_open_positions([])
        database.save_position_history([])
        return jsonify({"ok": True, "msg": "positions and history cleared"})

    @app.route("/api/positions/close-all", methods=["POST"])
    def api_positions_close_all():
        try:
            now = arrow.now().format("HH:mm:ss")
            genome = current_genome()
            leverage = genome.get("leverage", genome.get("general", {}).get("leverage", 3.0))
            fee = genome.get("fee", genome.get("general", {}).get("fee", 0.0015))
            closed = []
            for pos in list(local_state.get("open_positions", [])):
                pair = pos["pair"]
                entry = pos.get("entry", 0)
                if entry <= 0:
                    continue
                close = local_state.get("indicator_cache", {}).get(pair, {}).get("close")
                if not close:
                    close = entry
                pnl = (close / entry - 1) * leverage
                if pos["direction"] == "SHORT":
                    pnl = -pnl
                pnl -= fee * 2
                win = pnl > 0
                local_state["position_history"].append({
                    "pair": pair, "direction": pos["direction"],
                    "entry": entry, "exit": close,
                    "exit_reason": "MANUAL", "pnl": round(pnl * 100, 2),
                    "win": win, "closed_at": now
                })
                closed.append({"pair": pair, "pnl": round(pnl * 100, 2)})
                notify_signal(local_state, pair, pos["direction"], close, "TP", pnl * 100)
            local_state["open_positions"] = []
            local_state["position_history"] = local_state["position_history"][-200:]
            state_dirty[0] = True
            save_state()
            return jsonify({"ok": True, "closed": closed})
        except Exception as e:
            logger.error("[CLOSE_ALL] %s", e)
            return jsonify({"ok": False, "msg": str(e)}), 500

    @app.route("/api/strategy/toggle", methods=["POST"])
    def api_strategy_toggle():
        local_state["strategy_active"] = not local_state["strategy_active"]
        state_dirty[0] = True
        save_state()
        return jsonify({"active": local_state["strategy_active"]})

    @app.route("/api/mode", methods=["POST"])
    @limiter.limit("30 per minute")
    def api_mode():
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "msg": "Invalid request body"}), 400
        mode = data.get("mode", "safe")
        if mode not in ("safe", "live", "running"):
            return jsonify({"ok": False, "msg": "Invalid mode. Must be 'safe' or 'running'"}), 400
        local_state["mode"] = mode
        state_dirty[0] = True
        save_state()
        return jsonify({"mode": local_state["mode"]})

    @app.route("/api/pairs", methods=["POST"])
    @limiter.limit("10 per minute")
    def api_pairs_add():
        from okx_client import fetch_single_pair_candles
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "msg": "Invalid request body"}), 400
        pair = data.get("pair", "").upper().strip()
        if not pair:
            return jsonify({"ok": False, "msg": "No pair specified"}), 400
        if not validate_pair(pair):
            return jsonify({"ok": False, "msg": "Invalid pair format. Expected e.g. BTC-USDT-SWAP"}), 400
        if pair in local_state["trading_pairs"]:
            return jsonify({"ok": False, "msg": "Pair already active"}), 409
        try:
            candles = fetch_single_pair_candles(pair)
            if not candles or len(candles) < 10:
                return jsonify({"ok": False, "msg": f"{pair}无数据或数据不足，无法引入"})
            result = calculate_for_pair(pair, candles)
            if result.get("data_points", 0) == 0:
                return jsonify({"ok": False, "msg": f"{pair}无数据，无法引入"})
        except Exception as e:
            return jsonify({"ok": False, "msg": f"{pair}数据获取失败: {str(e)[:80]}"})
        local_state["trading_pairs"].append(pair)
        local_state["indicator_cache"][pair] = result
        state_dirty[0] = True
        save_state()
        return jsonify({"ok": True, "pairs": local_state["trading_pairs"]})

    @app.route("/api/pairs/add-all", methods=["POST"])
    @limiter.limit("1 per 5 minutes")
    def api_pairs_add_all():
        from okx_client import fetch_single_pair_candles
        from concurrent.futures import ThreadPoolExecutor, as_completed
        try:
            all_instruments = fetch_all_usdt_swap_instruments()
        except Exception as e:
            return jsonify({"ok": False, "msg": f"获取合约列表失败: {str(e)[:80]}"})
        existing = set(local_state.get("trading_pairs", []))
        to_add = [p for p in all_instruments if p not in existing]
        if not to_add:
            return jsonify({"ok": True, "added": 0, "skipped": 0, "failed": 0, "list": [], "skipped_list": [], "failed_list": []})
        to_add = to_add[:50]
        added, skipped, failed = [], [], []
        def _try_add(pair):
            try:
                candles = fetch_single_pair_candles(pair, retries=1)
                if not candles or len(candles) < 10:
                    return "no_candles"
                result = calculate_for_pair(pair, candles)
                if result.get("data_points", 0) == 0:
                    return "calc_failed"
                return ("ok", result)
            except Exception:
                return "err"
        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = {ex.submit(_try_add, p): p for p in to_add}
            for f in as_completed(futures):
                pair = futures[f]
                status = f.result()
                if isinstance(status, tuple) and status[0] == "ok":
                    local_state["trading_pairs"].append(pair)
                    local_state["indicator_cache"][pair] = status[1]
                    added.append(pair)
                elif status == "no_candles":
                    skipped.append(pair)
                else:
                    failed.append(pair)
        if added:
            state_dirty[0] = True
            save_state()
        return jsonify({
            "ok": True,
            "added": len(added),
            "skipped": len(skipped),
            "failed": len(failed),
            "list": added,
            "skipped_list": skipped,
            "failed_list": failed,
        })

    @app.route("/api/pairs/<path:pair>", methods=["DELETE"])
    def api_pairs_remove(pair):
        if pair in local_state["trading_pairs"]:
            local_state["trading_pairs"].remove(pair)
            state_dirty[0] = True
            save_state()
        return jsonify({"pairs": local_state["trading_pairs"]})

    @app.route("/api/users", methods=["POST"])
    def api_users_add():
        d = request.get_json()
        email = d.get("email", "").strip()
        if not email:
            return jsonify({"ok": False, "msg": "邮箱地址不能为空"})
        if any(secrets.compare_digest(u["email"], email) for u in local_state["safe_users"]):
            return jsonify({"ok": False, "msg": "邮箱已存在"})
        local_state["safe_users"].append({"email": email})
        state_dirty[0] = True
        save_state()
        return jsonify({"ok": True, "users": local_state["safe_users"]})

    @app.route("/api/users/<int:idx>", methods=["DELETE"])
    def api_users_remove(idx):
        if 0 <= idx < len(local_state["safe_users"]):
            local_state["safe_users"].pop(idx)
            state_dirty[0] = True
            save_state()
        return jsonify({"users": local_state["safe_users"]})

    @app.route("/api/test-email", methods=["POST"])
    def api_test_email():
        from notification.email import send_trade_notification, _is_configured
        if not _is_configured():
            return jsonify({"ok": False, "msg": "SMTP未配置，请先在系统设置中配置发件邮箱和授权码"})
        if not local_state.get("safe_users"):
            return jsonify({"ok": False, "msg": "无通知邮箱，请先在模式设置中添加接收邮箱"})
        users_snapshot = [u["email"] for u in local_state["safe_users"]]
        def _send_in_background():
            imported = __import__("notifications", fromlist=["send_trade_notification"])
            fn = imported.send_trade_notification
            body = "\n".join([
                "OKX量化网关测试邮件", "",
                "时间: 测试发送", "交易对: TEST-USDT-SWAP",
                "方向: 做多 (LONG)", "价格: 0.00", "类型: 开仓信号",
                "", "---", "来自 OKX 量化网关",
            ])
            for email in users_snapshot:
                fn(email, "【测试邮件】OKX量化网关", body)
        threading.Thread(target=_send_in_background, daemon=True).start()
        return jsonify({"ok": True, "msg": f"正在向 {len(users_snapshot)} 个邮箱发送测试邮件..."})

    @app.route("/api/accounts", methods=["GET"])
    def api_accounts_get():
        accounts = []
        for a in local_state["running_accounts"]:
            decrypted_key = _simple_decrypt(a.get("api_key", ""))
            accounts.append({
                "label": a.get("label", ""),
                "uid": a["uid"],
                "api_key": decrypted_key[:8] + "..." if decrypted_key else "***",
                "api_secret": "***",
                "passphrase": "***"
            })
        return jsonify({"accounts": accounts})

    @app.route("/api/accounts", methods=["POST"])
    @limiter.limit("5 per minute")
    def api_accounts_add():
        d = request.get_json()
        uid = d.get("uid", "").strip()
        api_key = d.get("api_key", "").strip()
        api_secret = d.get("api_secret", "").strip()
        passphrase = d.get("passphrase", "").strip()
        label = d.get("label", "").strip()
        if not uid or not api_key or not api_secret or not passphrase:
            return jsonify({"ok": False, "msg": "All API fields required"})
        local_state["running_accounts"].append({
            "label": label,
            "uid": uid,
            "api_key": _simple_encrypt(api_key),
            "api_secret": _simple_encrypt(api_secret),
            "passphrase": _simple_encrypt(passphrase)
        })
        state_dirty[0] = True
        save_state()
        accounts = []
        for a in local_state["running_accounts"]:
            decrypted_key = _simple_decrypt(a.get("api_key", ""))
            accounts.append({
                "label": a.get("label", ""),
                "uid": a["uid"],
                "api_key": decrypted_key[:8] + "..." if decrypted_key else "***",
                "api_secret": "***",
                "passphrase": "***"
            })
        return jsonify({"ok": True, "accounts": accounts})

    @app.route("/api/accounts/<int:idx>", methods=["DELETE"])
    def api_accounts_remove(idx):
        if 0 <= idx < len(local_state["running_accounts"]):
            local_state["running_accounts"].pop(idx)
            state_dirty[0] = True
            save_state()
        accounts = []
        for a in local_state["running_accounts"]:
            decrypted_key = _simple_decrypt(a.get("api_key", ""))
            accounts.append({
                "label": a.get("label", ""),
                "uid": a["uid"],
                "api_key": decrypted_key[:8] + "..." if decrypted_key else "***",
                "api_secret": "***",
                "passphrase": "***"
            })
        return jsonify({"accounts": accounts})

    @app.route("/api/log")
    def api_log():
        return jsonify({"log": local_state["strategy_log"]})

    @app.route("/api/log/clear", methods=["POST"])
    def api_log_clear():
        local_state["strategy_log"].clear()
        return jsonify({"ok": True})

    @app.route("/api/instruments")
    def api_instruments():
        instruments = fetch_all_usdt_swap_instruments()
        return jsonify({"instruments": sorted(instruments) if instruments else []})

    @app.route("/api/health")
    def api_health():
        from auth.credentials import get_credential_status, get_smtp_config, get_db_config, get_okx_config
        creds = get_credential_status()
        return jsonify({
            "status": "ok",
            "mode": local_state.get("mode", "safe"),
            "credentials_configured": any(creds.values()),
            "smtp_configured": get_smtp_config()["enabled"],
            "db_configured": bool(get_db_config().get("password")),
            "okx_configured": get_okx_config()["configured"],
        })

    @app.route("/api/credentials/status")
    def api_credentials_status():
        from auth.credentials import get_credential_status
        return jsonify(get_credential_status())

    @app.route("/api/credentials/values")
    def api_credentials_values():
        from auth.credentials import get_credential
        return jsonify({
            "smtp_sender": get_credential("smtp_sender") or "",
            "smtp_auth_code": get_credential("smtp_auth_code") or "",
            "smtp_host": get_credential("smtp_host") or "smtp.163.com",
            "smtp_port": get_credential("smtp_port") or "465",
            "db_host": get_credential("db_host") or "",
            "db_port": get_credential("db_port") or "",
            "db_user": get_credential("db_user") or "",
            "db_name": get_credential("db_name") or "",
        })

    @app.route("/api/credentials", methods=["POST"])
    @limiter.limit("5 per minute")
    def api_credentials_set():
        from auth.credentials import set_credential, CREDENTIAL_TYPES, apply_credentials_to_env
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "msg": "Invalid request body"}), 400

        cred_type = data.get("type")
        value = data.get("value")

        if not cred_type or value is None:
            return jsonify({"ok": False, "msg": "Missing type or value"}), 400

        if cred_type not in CREDENTIAL_TYPES:
            return jsonify({"ok": False, "msg": f"Invalid credential type: {cred_type}"}), 400

        if set_credential(cred_type, value):
            apply_credentials_to_env()
            return jsonify({"ok": True, "msg": f"Credential {cred_type} saved"})

        return jsonify({"ok": False, "msg": "Failed to save credential"}), 500

    @app.route("/api/credentials/<cred_type>", methods=["DELETE"])
    @limiter.limit("10 per minute")
    def api_credentials_delete(cred_type):
        from auth.credentials import remove_credential, CREDENTIAL_TYPES
        if cred_type not in CREDENTIAL_TYPES:
            return jsonify({"ok": False, "msg": f"Invalid credential type: {cred_type}"}), 400

        if remove_credential(cred_type):
            return jsonify({"ok": True, "msg": f"Credential {cred_type} removed"})

        return jsonify({"ok": False, "msg": "Failed to remove credential"}), 500

    @app.route("/api/credentials/smtp", methods=["GET"])
    def api_credentials_smtp():
        from auth.credentials import get_smtp_config
        config = get_smtp_config()
        return jsonify({
            "configured": config["enabled"],
            "sender": config["sender"] if config["sender"] else None,
        })

    @app.route("/api/credentials/okx", methods=["GET"])
    def api_credentials_okx():
        from auth.credentials import get_okx_config
        config = get_okx_config()
        return jsonify({
            "configured": config["configured"],
            "api_key": config["api_key"][:8] + "..." if config["api_key"] else None,
        })

    @app.route("/api/network/status")
    def api_network_status():
        from exchange.network import get_network_manager
        manager = get_network_manager()
        return jsonify(manager.get_status())

    @app.route("/api/strategy/params")
    def api_strategy_params():
        from strategy.manager import get_strategy_params
        return jsonify(get_strategy_params())

    @app.route("/api/strategy/params/summary")
    def api_strategy_params_summary():
        from strategy.manager import get_params_summary
        return jsonify(get_params_summary())

    @app.route("/api/strategy/params/<path:param_path>", methods=["GET"])
    def api_strategy_param_get(param_path):
        from strategy.manager import get_param
        value = get_param(param_path)
        if value is None:
            return jsonify({"ok": False, "msg": f"Parameter not found: {param_path}"}), 404
        return jsonify({"ok": True, "path": param_path, "value": value})

    @app.route("/api/strategy/params/<path:param_path>", methods=["PUT"])
    @limiter.limit("30 per minute")
    def api_strategy_param_set(param_path):
        from strategy.manager import set_param
        data = request.get_json()
        if not data or "value" not in data:
            return jsonify({"ok": False, "msg": "Missing value in request body"}), 400
        if set_param(param_path, data["value"]):
            return jsonify({"ok": True, "msg": f"Parameter {param_path} updated"})
        return jsonify({"ok": False, "msg": "Failed to update parameter"}), 500

    @app.route("/api/strategy/params", methods=["PUT"])
    @limiter.limit("10 per minute")
    def api_strategy_params_update():
        from strategy.manager import set_params
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "msg": "Invalid request body"}), 400
        if set_params(data):
            return jsonify({"ok": True, "msg": "Strategy parameters updated"})
        return jsonify({"ok": False, "msg": "Failed to update parameters"}), 500

    @app.route("/api/strategy/params/reset", methods=["POST"])
    @limiter.limit("5 per minute")
    def api_strategy_params_reset():
        from strategy.manager import reset_params
        if reset_params():
            return jsonify({"ok": True, "msg": "Strategy parameters reset to defaults"})
        return jsonify({"ok": False, "msg": "Failed to reset parameters"}), 500

    @app.route("/api/strategy/regime", methods=["GET"])
    def api_regime_get():
        from strategy.manager import get_regime_mode, get_all_regimes, get_general_params
        return jsonify({
            "mode": get_regime_mode(),
            "regimes": get_all_regimes(),
            "general": get_general_params(),
        })

    @app.route("/api/strategy/regime", methods=["PUT"])
    @limiter.limit("30 per minute")
    def api_regime_set():
        from strategy.manager import set_regime_mode
        data = request.get_json()
        if not data or "mode" not in data:
            return jsonify({"ok": False, "msg": "Missing mode"}), 400
        if set_regime_mode(data["mode"]):
            return jsonify({"ok": True, "msg": f"Regime mode set to {data['mode']}"})
        return jsonify({"ok": False, "msg": "Invalid mode (auto/bull/bear/range)"}), 400

    @app.route("/api/strategy/regime/<regime>", methods=["GET"])
    def api_regime_params_get(regime):
        from strategy.manager import get_regime_params
        if regime not in ("bull", "bear", "range"):
            return jsonify({"ok": False, "msg": "Invalid regime"}), 400
        return jsonify({"ok": True, "regime": regime, "params": get_regime_params(regime)})

    @app.route("/api/strategy/regime/<regime>", methods=["PUT"])
    @limiter.limit("30 per minute")
    def api_regime_params_set(regime):
        from strategy.manager import set_regime_params
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "msg": "Invalid request body"}), 400
        if set_regime_params(regime, data):
            return jsonify({"ok": True, "msg": f"Regime '{regime}' params updated"})
        return jsonify({"ok": False, "msg": "Invalid regime"}), 400

    @app.route("/settings")
    def settings_page():
        html = SETTINGS_TEMPLATE.replace("{{ csrf_token }}", "local-gateway-no-csrf")
        html = html.replace("{{ version }}", VERSION)
        return html

    @app.route("/api/config/export", methods=["GET"])
    def api_config_export():
        from strategy.manager import get_strategy_params
        from auth.credentials import get_credential_status
        return jsonify({
            "strategy": get_strategy_params(),
            "credentials": get_credential_status(),
            "state": {k: v for k, v in local_state.items() if k not in ["running_accounts", "price_cache", "indicator_cache"]},
        })

    @app.route("/api/config/import", methods=["POST"])
    @limiter.limit("5 per minute")
    def api_config_import():
        from strategy.manager import set_params
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "msg": "Invalid request body"}), 400
        results = {}
        try:
            if "strategy" in data:
                if set_params(data["strategy"]):
                    results["strategy"] = "updated"
                else:
                    results["strategy"] = "failed"
            if "state" in data:
                allowed_keys = ["mode", "trading_pairs", "strategy_active"]
                for key in allowed_keys:
                    if key in data["state"]:
                        local_state[key] = data["state"][key]
                results["state"] = "updated"
        except Exception as e:
            return jsonify({"ok": False, "msg": str(e)}), 400
        return jsonify({"ok": True, "results": results})

    @app.route("/api/models")
    def api_models_list():
        from model.manager import list_models
        return jsonify({"models": list_models()})

    @app.route("/api/models/active")
    def api_models_active():
        from model.manager import get_active_model
        model = get_active_model()
        if model:
            return jsonify({"ok": True, "model": model.get_info()})
        return jsonify({"ok": False, "msg": "No active model"})

    @app.route("/api/models/active", methods=["PUT"])
    def api_models_set_active():
        from model.manager import set_active_model
        data = request.get_json()
        if not data or "name" not in data:
            return jsonify({"ok": False, "msg": "Missing model name"}), 400
        name = data["name"]
        if not name or '..' in name or '/' in name or '\\' in name or len(name) > 64:
            return jsonify({"ok": False, "msg": "Invalid model name"}), 400
        if set_active_model(name):
            return jsonify({"ok": True, "msg": f"Active model set to {name}"})
        return jsonify({"ok": False, "msg": f"Model '{name}' not found"}), 404

    @app.route("/api/models/reload", methods=["POST"])
    @limiter.limit("10 per minute")
    def api_models_reload():
        from model.manager import reload_model
        data = request.get_json() or {}
        name = data.get("name")
        if reload_model(name):
            return jsonify({"ok": True, "msg": "Model reloaded"})
        return jsonify({"ok": False, "msg": "Failed to reload model"}), 500

    @app.route("/gateway-ui-test")
    def gateway_ui_test():
        import os
        ui_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gateway-ui-test.html")
        with open(ui_path, "r") as f:
            return f.read()

    @app.route("/gateway-ui-simple")
    def gateway_ui_simple():
        import os
        ui_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gateway-ui-simple.html")
        with open(ui_path, "r") as f:
            return f.read()

    @app.route("/static/<path:filename>")
    def serve_static(filename):
        import os
        static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
        return app.send_static_file(static_dir, filename)

    @app.route("/api/positions/clear-losses", methods=["POST"])
    def api_positions_clear_losses():
        """Clear losing records from position history."""
        with local_lock:
            before_count = len(local_state.get("position_history", []))
            local_state["position_history"] = [
                h for h in local_state.get("position_history", [])
                if h.get("win", False)
            ]
            after_count = len(local_state["position_history"])
            removed = before_count - after_count
            state_dirty[0] = True
            save_state()
        return jsonify({"ok": True, "removed": removed, "remaining": after_count})
