"""SMTP 提醒模块 — 参考 okx_quant_gateway/notification/email.py 设计。

与参考实现对齐的核心设计:
  * 配置全部来自环境变量, 无硬编码密钥
  * 全局单例 SMTP 连接 + 配置变更自动重建 (连接复用)
  * 线程锁保护 (网关 feed 线程 + FastAPI 请求线程并发)
  * 发送失败自动重置连接 (下次重连)
  * 信号/错误通知去重 (避免 30s 轮询刷屏)

环境变量:
  SMTP_SENDER     发件邮箱 + 授权码 (两者都设才启用)
  SMTP_AUTH_CODE  SMTP 授权码 (不是登录密码)
  SMTP_HOST       SMTP 服务器 (默认 smtp.163.com)
  SMTP_PORT       端口 (默认 465, SSL)
  SMTP_TO         收件人, 逗号分隔 (默认 = 发件人)

未配置时所有函数静默 no-op, 网关正常运行。
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import threading
import time
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path

logger = logging.getLogger(__name__)

from config import BASE_DIR

CONFIG_FILE: Path = Path(os.environ.get("SMTP_CONFIG_FILE") or str(BASE_DIR / "smtp_config.json"))

# ---- 配置 (环境变量为初值, configure() 可热更新) ----
_SMTP_SENDER = os.environ.get("SMTP_SENDER")
_SMTP_AUTH_CODE = os.environ.get("SMTP_AUTH_CODE")
_SMTP_HOST = os.environ.get("SMTP_HOST") or "smtp.163.com"
_SMTP_PORT = int(os.environ.get("SMTP_PORT") or "465")
_SMTP_TO = [t.strip() for t in (os.environ.get("SMTP_TO") or "").split(",") if t.strip()]

# ---- 连接复用状态 ----
_SMTP_LOCK = threading.Lock()
_SMTP_SEND_LOCK = threading.Lock()  # 串行化 sendmail, 防止多线程共用同一连接交错发送
_SMTP_INSTANCE: smtplib.SMTP | None = None
_SMTP_CONFIG_HASH: tuple | None = None
_SMTP_LAST_ERROR: str | None = None

# ---- 通知去重状态 (guard by _SMTP_LOCK) ----
_last_buy_coins: tuple[str, ...] | None = None
_last_error_key: tuple[str, str] | None = None


def _load_config_file() -> None:
    """启动时从配置文件加载运行时设置 (覆盖环境变量初值)."""
    global _SMTP_SENDER, _SMTP_AUTH_CODE, _SMTP_HOST, _SMTP_PORT, _SMTP_TO
    if not CONFIG_FILE.exists():
        return
    try:
        payload = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    if payload.get("sender"):
        _SMTP_SENDER = str(payload["sender"])
    if payload.get("auth_code"):
        _SMTP_AUTH_CODE = str(payload["auth_code"])
    if payload.get("host"):
        _SMTP_HOST = str(payload["host"])
    if payload.get("port"):
        _SMTP_PORT = int(payload["port"])
    recipients = [t.strip() for t in str(payload.get("to") or "").split(",") if t.strip()]
    if recipients:
        _SMTP_TO = recipients


def configure(sender: str = "", auth_code: str = "", host: str = "", port: int = 465, to: str = "") -> dict:
    """运行时更新 SMTP 配置并持久化. 返回新配置 (auth_code 脱敏).

    空字符串字段表示保留现值; 显式清空请调用 clear().
    """
    global _SMTP_SENDER, _SMTP_AUTH_CODE, _SMTP_HOST, _SMTP_PORT, _SMTP_TO
    with _SMTP_LOCK:
        if sender:
            _SMTP_SENDER = str(sender).strip()
        if auth_code:
            _SMTP_AUTH_CODE = str(auth_code).strip()
        if host:
            _SMTP_HOST = str(host).strip()
        if port:
            _SMTP_PORT = int(port)
        if to:
            _SMTP_TO = [t.strip() for t in str(to).split(",") if t.strip()]
        payload = {
            "sender": _SMTP_SENDER or "",
            "auth_code": _SMTP_AUTH_CODE or "",
            "host": _SMTP_HOST,
            "port": _SMTP_PORT,
            "to": ",".join(_SMTP_TO),
        }
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_FILE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(CONFIG_FILE)  # 原子替换, 防止写一半崩溃损坏配置
        except Exception as exc:
            logger.error("[SMTP] config save failed: %s", exc)
    reset()
    return get_config()


def clear() -> dict:
    """清空全部 SMTP 配置并删除持久化文件."""
    global _SMTP_SENDER, _SMTP_AUTH_CODE, _SMTP_TO
    with _SMTP_LOCK:
        _SMTP_SENDER = None
        _SMTP_AUTH_CODE = None
        _SMTP_TO = []
        try:
            if CONFIG_FILE.exists():
                CONFIG_FILE.unlink()
        except Exception:
            pass
    reset()
    return get_config()


def get_config() -> dict:
    """返回当前配置 (auth_code 脱敏)."""
    with _SMTP_LOCK:
        return {
            "configured": is_configured(),
            "sender": _SMTP_SENDER or "",
            "auth_code_set": bool(_SMTP_AUTH_CODE),
            "auth_code": ("*" * 8) if _SMTP_AUTH_CODE else "",
            "host": _SMTP_HOST,
            "port": _SMTP_PORT,
            "to": _SMTP_TO,
            "last_error": _SMTP_LAST_ERROR,
        }


def test_send() -> tuple[bool, str]:
    """发送测试邮件验证配置. 返回 (成功?, 详情)."""
    if not is_configured():
        return False, "SMTP 未配置 (需要 sender + auth_code)"
    ok = send_alert("【测试】量化网关 SMTP 配置验证", "这是一封来自 OKX 量化网关的测试邮件。\n若收到此邮件, 说明 SMTP 配置正确。")
    if ok:
        return True, "测试邮件已发送"
    return False, _SMTP_LAST_ERROR or "发送失败"


def is_configured() -> bool:
    """SMTP 是否已配置 (发件人 + 授权码都存在)."""
    return bool(_SMTP_SENDER and _SMTP_AUTH_CODE)


def _to_list() -> list[str]:
    return _SMTP_TO or ([_SMTP_SENDER] if _SMTP_SENDER else [])


def _config_hash() -> tuple | None:
    if not is_configured():
        return None
    return (_SMTP_SENDER, _SMTP_AUTH_CODE, _SMTP_HOST, _SMTP_PORT, tuple(_to_list()))


def _create_smtp() -> tuple[smtplib.SMTP | None, str | None]:
    """建立 SMTP 连接 (SSL or STARTTLS)."""
    try:
        if _SMTP_PORT == 465:
            conn = smtplib.SMTP_SSL(_SMTP_HOST, _SMTP_PORT, timeout=20)
        else:
            conn = smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=20)
            conn.starttls()
        conn.login(_SMTP_SENDER, _SMTP_AUTH_CODE)
        logger.info("[SMTP] Connected to %s:%s", _SMTP_HOST, _SMTP_PORT)
        return conn, None
    except Exception as exc:
        logger.error("[SMTP] Connection failed: %s", exc)
        return None, str(exc)


def _get_or_create() -> smtplib.SMTP | None:
    """复用连接; 配置变更或未建立时重建."""
    global _SMTP_INSTANCE, _SMTP_CONFIG_HASH, _SMTP_LAST_ERROR
    if not is_configured():
        _SMTP_LAST_ERROR = "SMTP not configured (missing SMTP_SENDER or SMTP_AUTH_CODE)"
        return None
    cfg_hash = _config_hash()
    with _SMTP_LOCK:
        if cfg_hash != _SMTP_CONFIG_HASH or _SMTP_INSTANCE is None:
            old = _SMTP_INSTANCE
            _SMTP_INSTANCE, err = _create_smtp()
            _SMTP_CONFIG_HASH = cfg_hash
            _SMTP_LAST_ERROR = err
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass
        return _SMTP_INSTANCE


def _reset_connection() -> None:
    global _SMTP_INSTANCE
    with _SMTP_LOCK:
        if _SMTP_INSTANCE is not None:
            try:
                _SMTP_INSTANCE.close()
            except Exception:
                pass
        _SMTP_INSTANCE = None


def send_alert(subject: str, body: str) -> bool:
    """发送一封通用告警邮件 (连接/发送失败自动重试一次, 应对首次连接瞬时失败)."""
    if not is_configured():
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(_SMTP_SENDER.split("@")[0]), _SMTP_SENDER))
    msg["To"] = ", ".join(_to_list())
    last_err: str | None = None
    for attempt in range(2):
        conn = _get_or_create()
        if conn is None:
            last_err = _SMTP_LAST_ERROR
            logger.warning("[SMTP] No connection (attempt %d): %s", attempt + 1, last_err)
        else:
            try:
                with _SMTP_SEND_LOCK:
                    conn.sendmail(_SMTP_SENDER, _to_list(), msg.as_string())
                logger.info("[SMTP] Sent alert to %s", _to_list())
                return True
            except Exception as exc:
                last_err = str(exc)
                logger.error("[SMTP] Send failed (attempt %d): %s", attempt + 1, exc)
                _reset_connection()
        if attempt == 0:
            time.sleep(1.5)
    logger.warning("[SMTP] Send failed after retries: %s", last_err)
    return False


def notify_signal(signals: list[dict], source: str | None = None) -> bool:
    """BUY 信号通知 (信号过弱不推送; 去重: BUY 币集合变化才发, 已推送过的币不重复).

    信号强度判定: model_prob 须达到策略阈值的一定比例 (默认 80% 以上) 才算强信号.
    弱信号(概率远低于模型阈值)不推送, 避免噪音邮件.

    signals: signal_weights() 返回的 list[dict]. 每项含 coin/signal/model_prob/
    threshold/close/take_profit/stop_loss. 邮件内容含买入价与止盈/止损卖出点位.
    """
    if not is_configured() or not signals:
        return False
    STRENGTH_RATIO = 0.80  # model_prob 需 >= threshold * 0.80 才算强信号
    buys: list[dict] = []
    for s in signals:
        if s.get("signal") != "BUY":
            continue
        prob = s.get("model_prob")
        thr = s.get("threshold")
        if prob is None:
            continue
        if thr is not None and float(prob) < float(thr) * STRENGTH_RATIO:
            continue
        buys.append(s)
    if not buys:
        return False
    buy_coins = tuple(sorted(s["coin"] for s in buys))
    global _last_buy_coins
    with _SMTP_LOCK:
        if buy_coins == _last_buy_coins:
            return False
        _last_buy_coins = buy_coins

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "OKX量化网关 — 买入信号", "",
        f"时间: {now}",
        f"数据源: {source or 'N/A'}",
        f"BUY 信号数: {len(buy_coins)}",
        "",
        "─── 买入信号与点位 ───",
    ]
    for s in buys:
        coin = s["coin"]
        entry = float(s.get("close") or 0)
        tp_pct = float(s.get("take_profit") or 0)
        sl_pct = float(s.get("stop_loss") or 0)
        tp_price = entry * (1 + tp_pct)
        sl_price = entry * (1 - sl_pct)
        prob = s.get("model_prob")
        prob_s = f"{prob:.4f}" if prob is not None else "N/A"
        thr = s.get("threshold")
        thr_s = f"{thr:.4f}" if thr is not None else "N/A"
        lines.extend([
            f"  币种: {coin}",
            f"  模型得分: {prob_s}  (阈值 {thr_s})",
            f"  买入价: {entry:.4f}",
            f"  止盈价(卖出): {tp_price:.4f}  (+{tp_pct*100:.1f}%)",
            f"  止损价: {sl_price:.4f}  (-{sl_pct*100:.1f}%)",
            "",
        ])
    lines.extend(["---", "来自 OKX 量化网关"])
    return send_alert("【买入信号】" + " / ".join(buy_coins), "\n".join(lines))


def notify_scanner(signals: list[dict], source: str | None = None) -> bool:
    """KDJ/MACD/RSI 扫描器 BUY 信号通知 (去重由调用方负责).

    signals: scanner.scan() 返回的 list[dict], 每项含 coin/close/k/d/j/dif/rsi.
    """
    if not is_configured() or not signals:
        return False
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "OKX量化网关 — KDJ超卖反转扫描", "",
        f"时间: {now}",
        f"数据源: {source or 'N/A'}",
        f"命中数: {len(signals)}",
        "",
        "─── 买入信号与指标 ───",
    ]
    for s in signals:
        coin = s["coin"]
        entry = float(s.get("close") or 0)
        lines.extend([
            f"  币种: {coin}",
            f"  现价: {entry:.4f}",
            f"  KDJ: K={s.get('k')} D={s.get('d')} J={s.get('j')} (均<30, 金叉)",
            f"  DIF: {s.get('dif')} (零轴下方)",
            f"  RSI12: {s.get('rsi')} (未跌破30)",
            "",
        ])
    lines.extend(["---", "来自 OKX 量化网关"])
    return send_alert("【KDJ扫描】" + " / ".join(s["coin"] for s in signals), "\n".join(lines))


def notify_error(msg: str, kind: str = "error") -> bool:
    """错误/告警通知 (去重: 同类同消息只发一次, 恢复后重置)."""
    if not is_configured() or not msg:
        return False
    global _last_error_key
    key = (kind, msg)
    with _SMTP_LOCK:
        if key == _last_error_key:
            return False
        _last_error_key = key

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    body = "\n".join([
        "OKX量化网关告警", "",
        f"时间: {now}",
        f"类型: {kind}",
        f"内容: {msg}",
        "",
        "---",
        "来自 OKX 量化网关",
    ])
    return send_alert(f"【{kind}】量化网关告警", body)


def clear_error_state() -> None:
    """错误恢复后调用, 允许同一错误再次触发通知."""
    global _last_error_key
    with _SMTP_LOCK:
        _last_error_key = None


def reset() -> None:
    """完全重置连接与去重状态 (测试/配置热更新用)."""
    global _last_buy_coins, _last_error_key
    _reset_connection()
    with _SMTP_LOCK:
        _last_buy_coins = None
        _last_error_key = None


_load_config_file()


if not is_configured():
    logger.info(
        "SMTP 提醒未配置 (SMTP_SENDER / SMTP_AUTH_CODE 未设置), 通知静默禁用"
    )
