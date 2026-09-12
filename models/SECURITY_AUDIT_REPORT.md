# 模型安全认证报告 (Model Security Audit Report)

**模型**: short_term_ta_ga_v49_44coin_atr (LightGBM)
**认证时间**: 2026-09-12 (复跑 `quant-local/model_security_audit.py`)
**认证对象**: `./models/`
**认证范围**: 完整性 / 鲁棒性 / 文件安全 / 统计有效性

---

## 0. 模型标识

| 属性 | 值 |
|---|---|
| 模型文件 | `short_term_ta_ga_model.txt` (197,145 字节, 183 棵树) |
| 基因文件 | `best_chromosome.json` (4,229 字节) |
| SHA256 (模型) | `fef09f5dfeea403b9248cb3a5316b6cc74c2490140ac5fe74af4550ab797958d` |
| SHA256 (基因) | `58f6bfad2abc413930d882a13912faacb8dcd2bffcbfe68be6b5aa1330097081` |
| seed | 37 |
| 特征数 | 25 (1 categorical + 24 numeric) |
| horizon | 3 (4h bars → 12h) |
| 训练宇宙 | 44 主流币 (okx_4h_4y_curated, 2023-01-01 → 2026-08-21) |
| 来源 | best_chromosome.json 冻结基因 → export_model.py 确定性导出 |
| manifest | `MODEL_MANIFEST.json` (certified_at 2026-09-11T11:32:42Z) |

---

## 1. 完整性认证 ✅ PASS (6/6)

| 检查项 | 结果 |
|---|---|
| SHA256 校验 (模型) | ✅ 匹配 manifest |
| SHA256 校验 (基因) | ✅ 匹配 manifest |
| manifest 存在 | ✅ |
| 文件权限 | ✅ 0o644 (组/其他只读) |
| 模型可加载 | ✅ 183 棵树 |
| 特征名完整 | ✅ 25 特征 |

**结论**: 模型文件未被篡改，可复现、可追溯。

---

## 2. 鲁棒性认证 ✅ PASS (9/9)

| 异常输入 | 结果 |
|---|---|
| 空 frames | ✅ 不崩溃 |
| 空 frame | ✅ 不崩溃 |
| 缺 close 列 | ✅ 不崩溃 |
| 全 NaN close | ✅ 不崩溃 |
| 极端值 (inf) | ✅ 不崩溃 |
| 单行 frame | ✅ 不崩溃 |
| 未知币名 | ✅ 不崩溃 (走 missing-category) |
| 少币 (3) | ✅ 不崩溃 |
| 正常 15 币 | ✅ 5 信号, model 方法 |

**结论**: 模型在异常/边界输入下不崩溃，鲁棒。

---

## 3. 文件安全认证 ✅ PASS (7/7)

| 扫描项 | 结果 |
|---|---|
| shell 命令注入 | ✅ 无 |
| URL/网络调用 | ✅ 无 |
| Python 代码注入 | ✅ 无 |
| SQL 注入 | ✅ 无 |
| 非预期二进制 | ✅ 无 |
| LightGBM 结构完整 | ✅ 197,145 字节纯文本树结构 |
| 文件可读文本 | ✅ 3,854 行 |

**结论**: 模型文件为纯数值树结构文本，无恶意代码、无后门、无注入。

---

## 4. 统计有效性 (来自 MODEL_MANIFEST.json 认证数据, 2026-09-11)

| 指标 | 值 | 说明 |
|---|---|---|
| sharpe | 8.44 | 回测 (组合层面) |
| return | +104.8% | 回测 |
| drawdown | 1.48% | 回测 |
| trades | 711 | 其中 SL 命中 69 / TP 命中 42 (真实触发) |
| rank_ic | +0.0012 | 横截面排序能力 (弱) |
| auc | 0.5414 | 略高于随机 |
| walk_forward IR | 1.33 | 滚动验证 |
| significance | PASS | bootstrap CI>0, permutation p=0.001 |
| deployment_status | DEPLOYABLE_CANDIDATE | manifest 标记 |
| research_only | true | manifest 标记 |

**注意**: AUC 0.541 / rank_IC 0.0012 表明模型预测力很弱，仅略优于随机；
manifest 明确标记 `research_only: true`。该模型适合研究/纸面跟踪，
**不应**作为真实资金交易的入场依据。

---

## 5. 综合认证结论

| 认证维度 | 结果 |
|---|---|
| 完整性 (防篡改/可追溯) | ✅ PASS |
| 鲁棒性 (异常输入) | ✅ PASS |
| 文件安全 (无后门/注入) | ✅ PASS |
| 统计-组合层面 (非过拟合) | ✅ PASS (manifest 认证) |
| 统计-单笔层面 | ⚠️ 弱 (AUC 0.541, rank_IC 0.0012) |

### 最终判定

**模型"安全"（工程层面）: PASS (22/22)** —— 无后门、无注入、鲁棒、可复现、可追溯，可安全部署到网关。

**模型"盈利可靠性"（统计层面）: 弱** —— 组合层面回测指标亮眼 (sharpe 8.44) 但预测力指标
(AUC 0.541 / rank_IC 0.0012) 表明信号极弱，manifest 标记 `research_only`。
网关中模型仅用于**横截面选币排名**，入场由 ADX 趋势确认门控，未直接以模型概率下单。

---

## 附: 认证产物

- `models/MODEL_MANIFEST.json` — 模型清单 (checksum + 元数据 + 认证指标)
- `quant-local/audit_result.json` — 完整性/鲁棒性/文件安全 22 项结果 (2026-09-12 复跑)
- `quant-local/model_security_audit.py` — 认证脚本 (可复跑)
- `quant-local/run_all_checks.py` — 统计检测入口 (可复跑)