"""Email notification system for OKX Quantitative Trading Gateway.

Uses yagmail for reliable SMTP delivery with automatic reconnection.
"""
import logging
import threading

import arrow
import yagmail

logger = logging.getLogger(__name__)

_SMTP_LOCK = threading.Lock()
_SMTP_INSTANCE = None
_SMTP_CONFIG_HASH = None
_SMTP_LAST_ERROR = None


def _get_smtp_config_hash():
    from auth.credentials import get_smtp_config
    cfg = get_smtp_config()
    if not cfg["enabled"]:
        return None
    return (cfg["sender"], cfg["auth_code"], cfg["host"], cfg["port"])


def _create_smtp():
    from auth.credentials import get_smtp_config
    cfg = get_smtp_config()
    if not cfg["enabled"]:
        return None, "SMTP not configured"
    try:
        kwargs = dict(
            user=cfg["sender"], password=cfg["auth_code"],
            host=cfg["host"], port=cfg["port"],
            smtp_ssl=(cfg["port"] == 465),
        )
        import socket
        default_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(10)
        try:
            instance = yagmail.SMTP(**kwargs)
        finally:
            socket.setdefaulttimeout(default_timeout)
        logger.info("[SMTP] Connected to %s:%s", cfg["host"], cfg["port"])
        return instance, None
    except Exception as e:
        logger.error("[SMTP] Connection failed: %s", e)
        return None, str(e)


def _get_or_create_smtp():
    global _SMTP_INSTANCE, _SMTP_CONFIG_HASH, _SMTP_LAST_ERROR
    config_hash = _get_smtp_config_hash()
    if config_hash is None:
        _SMTP_LAST_ERROR = "SMTP not configured (missing sender or auth_code)"
        return None
    with _SMTP_LOCK:
        if config_hash != _SMTP_CONFIG_HASH or _SMTP_INSTANCE is None:
            old = _SMTP_INSTANCE
            _SMTP_INSTANCE, err = _create_smtp()
            _SMTP_CONFIG_HASH = config_hash
            _SMTP_LAST_ERROR = err
            if old:
                try:
                    old.close()
                except Exception:
                    pass
        if _SMTP_INSTANCE is None:
            return None
        return _SMTP_INSTANCE


def _is_configured():
    from auth.credentials import get_smtp_config
    return get_smtp_config()["enabled"]


def send_trade_notification(to_email, subject, body_text):
    if not to_email:
        return False
    if not _is_configured():
        logger.debug("[EMAIL] SMTP not configured, skipping")
        return False
    smtp_conn = _get_or_create_smtp()
    if smtp_conn is None:
        logger.warning("[EMAIL] No SMTP connection: %s", _SMTP_LAST_ERROR)
        _record_error(f"SMTP不可用: {_SMTP_LAST_ERROR}")
        return False
    from auth.credentials import get_smtp_config
    cfg = get_smtp_config()
    try:
        smtp_conn.send(
            to=to_email, subject=subject,
            contents=body_text,
        )
        logger.info("[EMAIL] Sent to %s", to_email)
        return True
    except Exception as e:
        logger.error("[EMAIL] Send failed to %s: %s", to_email, e)
        _record_error(f"邮件发送失败: {e}")
        _reset_connection()
        return False


def _reset_connection():
    global _SMTP_INSTANCE
    with _SMTP_LOCK:
        if _SMTP_INSTANCE:
            try:
                _SMTP_INSTANCE.close()
            except Exception:
                pass
        _SMTP_INSTANCE = None


def _record_error(msg):
    try:
        from core.state import state_lock, state as _state
        with state_lock:
            _state["last_error"] = msg
    except Exception:
        pass


def reset_smtp_connection():
    global _SMTP_INSTANCE, _SMTP_CONFIG_HASH
    with _SMTP_LOCK:
        if _SMTP_INSTANCE:
            try:
                _SMTP_INSTANCE.close()
            except Exception:
                pass
        _SMTP_INSTANCE = None
        _SMTP_CONFIG_HASH = None
    logger.info("[SMTP] Connection reset")


def notify_signal(state_ref, pair, direction, price, sig_type, pnl=None, indicator_data=None):
    if not _is_configured():
        return
    if sig_type == "HOLD":
        return

    from auth.credentials import get_smtp_config
    cfg = get_smtp_config()
    sender = cfg["sender"]
    safe_users = state_ref.get("safe_users", [])
    if not safe_users:
        safe_users = [{"email": sender}]
        logger.info("[EMAIL] No safe_users, falling back to sender: %s", sender)

    now = arrow.now().format("YYYY-MM-DD HH:mm:ss")
    direction_cn = "观望" if direction == "HOLD" else ("做多" if direction == "LONG" else "做空")
    type_cn = {
        "OPEN": "开仓信号", "TP": "止盈平仓", "SL": "止损平仓",
        "TIME_TP": "时间止盈", "LONG": "做多信号", "SHORT": "做空信号",
        "HOLD": "信号未达阈值",
    }.get(sig_type, sig_type)
    subject = f"【{type_cn}】{pair} {direction_cn}"

    def _safe(val):
        if val is None or val in ("None", "null"):
            return "N/A"
        return val

    body_lines = [
        "OKX量化网关信号通知", "",
        f"时间: {now}", f"交易对: {pair}",
        f"方向: {direction_cn} ({direction})",
        f"价格: {_safe(price)}", f"类型: {type_cn}",
    ]

    genome = state_ref.get("genome", {})
    ind_cache = state_ref.get("indicator_cache", {})
    pair_ind = (ind_cache.get(pair) or ind_cache.get(pair.replace("-USDT-SWAP", ""))) if ind_cache else {}
    last_update = state_ref.get("last_update", "N/A")

    if sig_type in ("OPEN", "TP", "SL", "LONG", "SHORT"):
        body_lines.extend(["", "─── 止盈止损 ───", f"杠杆倍数: {_safe(genome.get('leverage', 'N/A'))}"])

    pos = next((p for p in state_ref.get("open_positions", []) if p["pair"] == pair), None)
    if pos:
        body_lines.append(f"止损: {pos.get('sl', 0):.10f}")
        body_lines.append(f"止盈: {pos.get('tp', 0):.10f}")

    if pnl is not None:
        body_lines.append(f"盈亏: {'+' if pnl >= 0 else ''}{pnl:.2f}%")

    kline_ts = None
    if pair_ind:
        kline_ts = pair_ind.get("ts")
    elif indicator_data and indicator_data.get("ts"):
        kline_ts = indicator_data.get("ts")

    if sig_type == "OPEN" and (kline_ts or last_update):
        body_lines.append("")
        body_lines.append("─── 时间戳 ───")
        if kline_ts:
            body_lines.append(f"数据获取时间 (kline): {_safe(kline_ts)}")
        if last_update:
            body_lines.append(f"计算时间 (刷新): {_safe(last_update)}")

    body_lines.extend(["", "---", "来自 OKX 量化网关"])
    body = "\n".join(str(line) for line in body_lines)

    seen = set()
    for user in safe_users:
        email = user.get("email") or str(user)
        if email in seen:
            continue
        seen.add(email)
        send_trade_notification(email, subject, body)
