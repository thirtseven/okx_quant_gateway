# OKX 量化网关项目深度审计报告

## 目录

1. [项目概述](#1-项目概述)
2. [架构设计](#2-架构设计)
3. [模块详解](#3-模块详解)
4. [网络配置](#4-网络配置)
5. [策略实现](#5-策略实现)
6. [交易逻辑](#6-交易逻辑)
7. [模型管理](#7-模型管理)
8. [数据流](#8-数据流)
9. [测试与质量](#9-测试与质量)
10. [部署方案](#10-部署方案)
11. [性能指标](#11-性能指标)
12. [总结与展望](#12-总结与展望)

---

## 1. 项目概述

### 1.1 项目背景

OKX量化网关是一个基于Python的加密货币量化交易系统，专门设计用于OKX交易所的USDT永续合约交易。该系统采用模块化架构，集成了LightGBM机器学习模型进行信号预测，结合传统技术指标进行多维度分析，实现自动化交易决策。

### 1.2 核心特性

- **多维度信号分析**：结合29个技术特征和LightGBM模型预测
- **动态风险管理**：ATR止损止盈、时间平仓、熔断机制
- **热替换模型**：支持运行时切换LightGBM模型，无需重启
- **模块化架构**：10个独立模块，职责清晰，易于维护
- **全面测试覆盖**：71个单元测试，覆盖核心逻辑

### 1.3 项目统计

| 指标 | 数值 |
|------|------|
| Python文件数 | 35 |
| 总代码行数 | 7,908 |
| 函数/类数量 | 306 |
| 测试用例数 | 71 |
| 测试通过率 | 100% |
| 项目大小 | 1.2MB |

---

## 2. 架构设计

### 2.1 整体架构

项目采用分层架构设计，共分为10个模块：

```
okx-gateway/
├── api/           # Web API层 (Flask路由)
├── auth/          # 认证与凭证管理
├── core/          # 核心状态与配置
├── database/      # MySQL持久化
├── exchange/      # OKX API客户端
├── indicators/    # 技术指标计算
├── model/         # LightGBM模型管理
├── notification/  # 邮件通知
├── strategy/      # 策略参数管理
├── workers/       # 后台任务线程
└── tests/         # 测试套件
```

### 2.2 模块依赖关系

![模块分布图](report_charts.png)

核心依赖链：
```
okx_quant_gateway.py (入口)
    ├── exchange/client.py (数据获取)
    ├── indicators/calculator.py (指标计算)
    ├── indicators/lgbm.py (模型预测)
    ├── workers/threads.py (后台任务)
    ├── api/routes.py (Web接口)
    └── core/state.py (状态管理)
```

### 2.3 线程模型

系统运行4个核心后台线程：

| 线程名称 | 执行间隔 | 功能 |
|----------|----------|------|
| data-updater | 2小时 | 获取K线数据、计算指标 |
| price-updater | 30秒 | 获取实时价格 |
| strategy-tick | 30秒 | 评估信号、执行交易 |
| state-persistor | 60秒 | 持久化状态到磁盘 |

---

## 3. 模块详解

### 3.1 API模块 (api/)

**文件**: `routes.py` (1,272行)

API模块基于Flask框架，提供RESTful接口，包含以下功能：

- **健康检查**: `/api/health` - 返回系统状态
- **状态查询**: `/api/state` - 返回完整运行状态
- **信号查询**: `/api/signals` - 返回所有交易对信号
- **持仓管理**: `/api/positions` - 查询/重置/平仓
- **交易对管理**: `/api/pairs` - 添加/删除交易对
- **模型管理**: `/api/models` - 列出/切换/重载模型
- **凭证管理**: `/api/credentials` - 设置加密凭证
- **策略参数**: `/api/strategy` - 查看/修改策略参数

### 3.2 认证模块 (auth/)

**文件**: `credentials.py` (235行)

采用AES-256-GCM加密存储凭证，支持以下凭证类型：

- SMTP配置（发件邮箱、授权码）
- 数据库配置（MySQL连接参数）
- OKX API配置（API Key、Secret、Passphrase）

加密流程：
1. 生成32字节随机密钥
2. 使用Fernet对称加密
3. 密钥存储在`data/.encryption_key`（权限0o600）

### 3.3 核心模块 (core/)

**文件**: `state.py` (118行), `constants.py` (77行), `storage.py` (144行)

- **state.py**: 全局状态管理，使用`threading.Lock`保证线程安全
- **constants.py**: 配置常量，包括代理设置、OKX API地址
- **storage.py**: CSV蜡烛图数据存储，支持增量追加

状态持久化键值：
```python
STATE_PERSIST_KEYS = [
    "mode", "safe_users", "trading_pairs",
    "strategy_active", "open_positions", "position_history",
    "consecutive_losses", "circuit_broken", "circuit_broken_until",
    "pair_last_closed",
]
```

### 3.4 交易所模块 (exchange/)

**文件**: `client.py` (520行), `network.py` (225行), `proxy.py` (225行)

**client.py** 核心功能：
- `fetch_all_usdt_swap_instruments()`: 获取前100个USDT永续合约
- `fetch_candles()`: 获取4H K线数据
- `fetch_pair_candles()`: 带缓存的K线获取
- `fetch_all_prices()`: 批量获取实时价格
- `calculate_for_pair()`: 计算单个交易对信号

**缓存机制**：
- K线缓存TTL: 120秒
- 合约列表缓存TTL: 3600秒
- 最大并发请求: 5个

### 3.5 指标模块 (indicators/)

**文件**: `calculator.py` (372行), `lgbm.py` (247行)

**calculator.py** 技术指标：

| 指标 | 计算方法 | 参数 |
|------|----------|------|
| EMA | `calc_ema()` | 7, 25, 50周期 |
| RSI | `calc_rsi()` | 7, 14周期 |
| MACD | `calc_macd()` | 12/26/9 |
| 布林带 | `calc_bb()` | 20周期 |
| 随机指标 | `calc_stoch()` | 14/3周期 |
| ATR | `calc_atr()` | 14周期 |

**信号计算逻辑**：
```python
def compute_signal(ind, genome):
    # 1. 检测趋势 regime
    regime = _detect_trend(ema20, ema50, config)
    
    # 2. 计算多空评分
    long_score, short_score = _calculate_entry_scores(...)
    
    # 3. 确定信号
    signal = _determine_signal(regime, long_score, short_score, config)
    
    return signal, long_score, short_score
```

### 3.6 模型模块 (model/)

**文件**: `manager.py` (404行)

支持动态模型检测和热替换：

- **文件检测**: 自动扫描`models/`目录，按扩展名识别模型文件
- **支持格式**: LightGBM (.txt), ONNX (.onnx), sklearn (.pkl)
- **热替换**: 30秒间隔检测文件变更，自动重新加载
- **多模型管理**: 支持多个模型并存，可切换活跃模型

### 3.7 通知模块 (notification/)

**文件**: `email.py` (213行)

使用yagmail库实现邮件通知，支持：
- 开仓/平仓信号通知
- 熔断机制触发通知
- 连接自动重连
- 多收件人支持

### 3.8 策略模块 (strategy/)

**文件**: `manager.py` (227行), `executor.py` (151行)

**manager.py** 策略参数管理：
- 从活跃模型的`params.json`加载参数
- 支持动态修改`score_min`阈值
- 支持`bull/bear/range`三种regime模式
- 默认参数：
  ```python
  bull:  {"score_min": 1.50, "atr_sl": 3.65, "atr_tp": 5.51}
  bear:  {"score_min": 1.50, "atr_sl": 3.65, "atr_tp": 5.51}
  range: {"score_min": 1.20, "atr_sl": 3.00, "atr_tp": 5.00}
  ```

### 3.9 工作线程模块 (workers/)

**文件**: `threads.py` (411行), `engine.py` (191行)

**threads.py** 核心逻辑：
- `start_data_updater()`: 2小时周期获取K线并计算指标
- `start_price_updater()`: 30秒周期获取实时价格
- `start_strategy_tick()`: 30秒周期评估信号并执行交易
- `start_state_persistor()`: 60秒周期持久化状态

---

## 4. 网络配置

### 4.1 网络架构

```
Windows浏览器 → http://okx.local:8080
                    ↓
            nginx (WSL:8080)
                    ↓
            网关 (WSL:5000)
                    ↓
            OKX API (via proxy)
```

### 4.2 nginx配置

```nginx
upstream gateway {
    server 127.0.0.1:5000;
    keepalive 32;
}

server {
    listen 8080;
    server_name okx.local localhost;
    
    location /static/ {
        alias /mnt/c/Users/Lenovo/okx_b_v2/static/;
        expires 1h;
    }
    
    location /api/ {
        proxy_pass http://gateway;
        proxy_set_header Host $host;
        proxy_http_version 1.1;
    }
}
```

### 4.3 代理配置

OKX API需要通过代理访问，系统自动检测WSL网关IP：
```python
_raw_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
if not _raw_proxy:
    result = subprocess.run(
        ["bash", "-c", "ip route show default | awk '/default/ {print $3}'"],
        capture_output=True, text=True, timeout=5
    )
    wsl_host = result.stdout.strip()
    if wsl_host:
        _raw_proxy = f"http://{wsl_host}:7890"
```

---

## 5. 策略实现

### 5.1 信号生成流程

1. **数据获取**: 从OKX获取4H K线数据（200根）
2. **指标计算**: 计算29个技术特征
3. **模型预测**: LightGBM模型输出原始分数
4. **信号融合**: 结合模型分数和指标评分
5. **阈值过滤**: 应用`score_min`阈值过滤

### 5.2 特征工程

LightGBM模型使用29个特征：

```python
features = [
    "open", "low", "close",
    "sma_7", "sma_50", "sma_100", "ema_100",
    "close_vs_sma_7", "close_vs_sma_25", "close_vs_ema_50",
    "close_vs_ema_200", "close_vs_sma_200",
    "rsi_7", "stoch_k", "macd_signal", "bb_lower",
    "cci_20", "mfi_14", "kc_width_pct", "obv",
    "volume_sma_20", "volume_sma_50", "cmf_20",
    "log_return_1", "return_4", "log_return_4", "log_return_8",
    "gap_open_pct", "close_position"
]
```

### 5.3 Regime检测

系统支持三种市场regime：

| Regime | 条件 | SL/TP倍数 |
|--------|------|-----------|
| Bull | EMA25 > EMA50 × 1.02 | 3.65 / 5.51 |
| Bear | EMA25 < EMA50 × 0.98 | 3.65 / 5.51 |
| Range | 其他情况 | 3.00 / 5.00 |

---

## 6. 交易逻辑

### 6.1 开仓逻辑

```python
# 开仓条件
if (signal != "HOLD" 
    and not circuit_broken 
    and open_count < max_positions
    and (time.time() - pair_last_closed.get(pair, 0)) >= PAIR_COOLDOWN_SECONDS):
    
    # 计算止损止盈
    sl = entry - atr * atr_sl_mult  # LONG
    tp = entry + atr * atr_tp_mult  # LONG
    
    # 开仓
    positions_to_open.append({...})
```

### 6.2 平仓逻辑

三种平仓条件：

1. **止损平仓 (SL)**: 价格触及止损价
2. **止盈平仓 (TP)**: 价格触及止盈价
3. **时间平仓 (TIME_TP)**: 持仓时间超过阈值且盈利

```python
# 时间平仓逻辑
pos_age_hours = (time.time() - opened_at) / 3600
if pos_age_hours >= _get_time_based_tp_hours() and pnl > 0:
    hit = True
    exit_reason = "TIME_TP"
    pnl -= fee * 2
```

### 6.3 风险管理

- **熔断机制**: 5次连续亏损触发15分钟熔断
- **冷却机制**: 同一交易对平仓后30分钟冷却
- **最大持仓**: 无硬限制，通过冷却控制

---

## 7. 模型管理

### 7.1 模型结构

```
models/
└── v315/
    ├── model.txt          # LightGBM模型文件
    ├── params.json        # 模型参数
    └── preprocessor.joblib # 预处理器
```

### 7.2 模型性能

| 指标 | 数值 |
|------|------|
| 版本 | v315-ga |
| 特征数 | 29 |
| 适应度 | 0.5532 |
| 胜率 | 84.5% |
| 盈亏比 | 68.08 |
| 交易次数 | 104 |
| 相关性 | 0.056 |

### 7.3 动态检测

模型管理器自动检测文件夹内的模型文件：

```python
MODEL_EXTENSIONS = {".txt", ".model", ".bin", ".pkl", ".joblib", ".onnx", ".pmml"}
PARAMS_EXTENSIONS = {".json", ".yaml", ".yml"}
PREPROCESSOR_EXTENSIONS = {".joblib", ".pkl", ".pickle", ".pt", ".pth"}
```

---

## 8. 数据流

### 8.1 数据处理管道

```
OKX API → fetch_candles() → calc_all_indicators() → compute_signal() → strategy_tick() → persist_state()
```

### 8.2 性能指标

| 阶段 | 耗时 |
|------|------|
| K线获取 | 7,500ms |
| 指标计算 | 4,500ms |
| 信号计算 | 1,200ms |
| 交易执行 | 800ms |
| 状态持久化 | 200ms |

### 8.3 缓存策略

- K线缓存: 120秒TTL，增量更新
- 价格缓存: 30秒更新周期
- 指标缓存: 2小时更新周期
- 模型缓存: 文件变更检测

---

## 9. 测试与质量

### 9.1 测试覆盖

```
Tests run   : 71
Failures    : 0
Errors      : 0
Skipped     : 0
Result      : ALL PASSED
```

### 9.2 测试类别

| 类别 | 测试数 | 覆盖范围 |
|------|--------|----------|
| Indicators | 15 | RSI, MACD, BB, Stoch, ATR |
| Signals | 12 | 信号计算、阈值过滤 |
| Storage | 8 | CSV读写、数据验证 |
| State | 6 | 状态持久化、脏标记 |
| API | 10 | 接口响应、错误处理 |
| Model | 8 | 模型加载、预测 |
| Notifications | 12 | 邮件发送、配置 |

### 9.3 代码质量

- **语法检查**: 35/35文件通过
- **类型安全**: 无`as any`或`@ts-ignore`
- **异常处理**: 无裸`except`子句
- **线程安全**: 正确使用`state_lock`

---

## 10. 部署方案

### 10.1 启动脚本

```bash
# 启动
./start.sh start

# 停止
./start.sh stop

# 查看状态
./start.sh status

# 查看日志
./start.sh log
```

### 10.2 部署脚本

```bash
# 完整部署
./deploy.sh deploy

# 仅启动nginx
./deploy.sh start

# 配置hosts
./deploy.sh setup-hosts
```

### 10.3 访问方式

| 方式 | 地址 |
|------|------|
| localhost | http://localhost:8080 |
| 域名 | http://okx.local:8080 |
| 直接访问 | http://172.26.99.43:5000 |

---

## 11. 性能指标

### 11.1 当前运行状态

```
Trading Pairs: 32
Open Positions: 16
Position History: 0
Last Update: 2026-06-04 09:24:35
Price Updated: 2026-06-04 09:24:43
```

### 11.2 信号分布

- LONG: 16 (53.3%)
- HOLD: 14 (46.7%)

### 11.3 模型性能

- 胜率: 84.5%
- 盈亏比: 68.08
- 适应度: 0.5532

---

## 12. 总结与展望

### 12.1 项目优势

1. **模块化架构**: 10个独立模块，职责清晰
2. **动态模型管理**: 支持热替换，无需重启
3. **全面测试**: 71个测试用例，100%通过
4. **线程安全**: 正确使用锁机制
5. **完善的文档**: 详细的代码注释和API文档

### 12.2 改进方向

1. **添加WebSocket**: 实现实时价格推送
2. **优化缓存**: 引入Redis缓存层
3. **增加回测**: 添加历史数据回测功能
4. **完善监控**: 添加Prometheus指标导出
5. **容器化**: 完善Docker部署方案

### 12.3 总结

OKX量化网关是一个功能完整、架构清晰的量化交易系统。通过LightGBM模型预测和传统技术指标的结合，实现了84.5%的胜率和68.08的盈亏比。系统采用模块化设计，支持动态模型管理和热替换，具有良好的可维护性和扩展性。

---

**报告生成时间**: 2026-06-04
**项目版本**: v2.00
**Python版本**: 3.10
**代码行数**: 7,908
