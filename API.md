# OKX 量化网关 API 文档

## 基础信息

- **Base URL**: `http://172.26.99.43:5000` 或 `http://okx.local:8080`
- **Content-Type**: `application/json`
- **认证**: 无需认证（本地网关）

---

## 健康检查

### GET /api/health

网关健康状态检查。

**响应**:
```json
{
  "status": "ok",
  "mode": "safe",
  "credentials_configured": false,
  "db_configured": false,
  "okx_configured": false,
  "smtp_configured": false
}
```

---

## 市场数据

### GET /api/state

获取网关完整状态。

**响应**:
```json
{
  "mode": "safe",
  "strategy_active": true,
  "trading_pairs": ["BTC-USDT-SWAP", ...],
  "open_positions": [...],
  "position_history": [...],
  "consecutive_losses": 0,
  "circuit_broken": false,
  "last_update": "2026-06-02 10:38:00",
  "price_updated": "2026-06-02 10:38:12"
}
```

### GET /api/prices

获取所有交易对实时价格。

**响应**:
```json
{
  "prices": {
    "BTC-USDT-SWAP": {
      "inst_id": "BTC-USDT-SWAP",
      "last": "70810.4",
      "open24h": "73490.1",
      "vol24h": "13165948.82",
      "ts": "1780367894208"
    }
  },
  "price_updated": "2026-06-02 10:38:12",
  "price_fetch_ms": 408
}
```

### GET /api/instruments

获取可用交易对列表（OKX USDT-SWAP前100）。

**响应**:
```json
{
  "instruments": ["BTC-USDT-SWAP", "ETH-USDT-SWAP", ...]
}
```

### GET /api/indicators

获取指标计算状态。

**响应**:
```json
{
  "last_update": "2026-06-02 10:38:00",
  "timing": {
    "fetch_ms": 1648,
    "calc_ms": 7563,
    "total_ms": 9211,
    "pairs": 32
  },
  "pair_count": 32,
  "refreshing": false,
  "interval_seconds": 7200
}
```

---

## 信号与策略

### GET /api/signals

获取所有交易对信号。

**响应**:
```json
{
  "signals": {
    "BTC-USDT-SWAP": {
      "signal": "LONG",
      "norm_long": 44.4,
      "norm_short": -0.5152,
      "rsi_14": 24.67,
      "close": 70840.1,
      "regime": "range"
    }
  },
  "thresholds": {
    "regime_mode": "auto",
    "regimes": {
      "bull": {"score_min": 1.5, ...},
      "bear": {"score_min": 1.5, ...},
      "range": {"score_min": 1.2, ...}
    }
  },
  "computing": false
}
```

### GET /api/strategy/model

获取当前活跃模型信息。

**响应**:
```json
{
  "loaded": true,
  "version": "v315-ga",
  "status": "complete",
  "model": {
    "max_depth": 2,
    "n_estimators": 100,
    "num_leaves": 4,
    "learning_rate": 0.03
  },
  "trading": {
    "fee": 0.0015,
    "max_hold": 48,
    "position": "long_only"
  },
  "n_features": 29,
  "feature_names": ["open", "low", "close", ...],
  "test": {
    "win_rate": 0.845,
    "profit_factor": 68.07,
    "correlation": 0.056,
    "trades": 104,
    "pnl": 0.113,
    "profitable": true
  },
  "fitness": 0.553,
  "evolution": {
    "generations": 40,
    "final_fitness": 0.553
  }
}
```

### POST /api/strategy/toggle

切换策略激活状态。

**响应**:
```json
{"active": true}
```

### PATCH /api/threshold

修改信号阈值。

**请求**:
```json
{"score_threshold": 1.5}
```

**响应**:
```json
{"ok": true, "score_threshold": 1.5}
```

---

## 持仓管理

### GET /api/positions

获取持仓信息。

**响应**:
```json
{
  "open_positions": [
    {
      "pair": "BTC-USDT-SWAP",
      "direction": "LONG",
      "entry": 70851.1,
      "sl": 68405.4,
      "tp": 74927.3,
      "opened_at": "2026-06-02 10:38:11"
    }
  ],
  "open_count": 11,
  "total_closed": 0,
  "win_rate": 0,
  "total_pnl": 0
}
```

### POST /api/positions/reset

重置持仓历史。

**响应**:
```json
{"ok": true, "msg": "Position history cleared"}
```

### POST /api/positions/close-all

平仓所有持仓。

**响应**:
```json
{"ok": true, "closed": 11}
```

---

## 交易对管理

### POST /api/pairs

添加单个交易对。

**请求**:
```json
{"pair": "BTC-USDT-SWAP"}
```

**响应**:
```json
{"ok": true, "pairs": ["BTC-USDT-SWAP", ...]}
```

### POST /api/pairs/add-all

批量添加热门交易对（前100）。

**响应**:
```json
{"ok": true, "added": 50, "total": 84}
```

### DELETE /api/pairs/{pair}

删除交易对。

**响应**:
```json
{"pairs": ["BTC-USDT-SWAP", ...]}
```

---

## 用户管理

### GET /api/users

获取通知邮箱列表。

### POST /api/users

添加通知邮箱。

**请求**:
```json
{"email": "user@example.com"}
```

### DELETE /api/users/{idx}

删除通知邮箱。

---

## 凭证管理

### GET /api/credentials/status

获取凭证配置状态。

**响应**:
```json
{
  "smtp_sender": true,
  "smtp_auth_code": true,
  "okx_api_key": false,
  ...
}
```

### POST /api/credentials

设置凭证。

**请求**:
```json
{
  "smtp_sender": "user@163.com",
  "smtp_auth_code": "xxxxxx"
}
```

---

## 模型管理

### GET /api/models

列出所有可用模型。

### GET /api/models/active

获取当前活跃模型。

### PUT /api/models/active

切换活跃模型。

**请求**:
```json
{"name": "v326"}
```

### POST /api/models/reload

重新加载模型。

---

## 日志

### GET /api/log

获取策略日志。

### POST /api/log/clear

清空策略日志。

---

## 模式切换

### POST /api/mode

切换运行模式。

**请求**:
```json
{"mode": "safe"}  // 或 "running"
```

---

## 邮件测试

### POST /api/test-email

发送测试邮件。

---

## 配置导出/导入

### GET /api/config/export

导出当前配置。

### POST /api/config/import

导入配置。

---

## 错误响应

所有错误响应格式：
```json
{
  "ok": false,
  "msg": "错误描述"
}
```

常见HTTP状态码：
- `200` - 成功
- `400` - 请求参数错误
- `404` - 资源不存在
- `409` - 冲突（如交易对已存在）
- `500` - 服务器内部错误
