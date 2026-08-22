# 模型安全认证报告 (Model Security Audit Report)

**模型**: short_term_ta_ga_v30 (LightGBM)
**认证时间**: 2026-08-20T14:26 UTC
**认证对象**: `./models/`
**认证范围**: 完整性 / 鲁棒性 / 文件安全 / 统计有效性

---

## 0. 模型标识

| 属性 | 值 |
|---|---|
| 模型文件 | `short_term_ta_ga_model.txt` (68,755 字节, 64 棵树) |
| 基因文件 | `best_chromosome.json` (4,156 字节) |
| SHA256 (模型) | `1eeed8f02209edf36a922bc57329f07cb4238e1231ba8b679dab2d8ac1b172d9` |
| SHA256 (基因) | `e0f0115b98c91d94309c38cf8deac9c1e411e865eeb82d29dfc77508ac852786` |
| seed | 37 |
| 特征数 | 25 (1 categorical + 24 numeric) |
| horizon | 3 (4h bars → 12h) |
| 来源 | best_chromosome.json 冻结基因 → export_model.py 本地确定性重拟合 |

---

## 1. 完整性认证 ✅ PASS (6/6)

| 检查项 | 结果 |
|---|---|
| SHA256 校验 (模型) | ✅ 匹配 manifest |
| SHA256 校验 (基因) | ✅ 匹配 manifest |
| manifest 存在 | ✅ |
| 文件权限 | ✅ 0o644 (组/其他只读) |
| 模型可加载 | ✅ 64 棵树 |
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
| 正常 15 币 | ✅ 3 信号, model 方法 |

**结论**: 模型在异常/边界输入下不崩溃，鲁棒。

---

## 3. 文件安全认证 ✅ PASS (6/6)

| 扫描项 | 结果 |
|---|---|
| shell 命令注入 | ✅ 无 |
| URL/网络调用 | ✅ 无 |
| Python 代码注入 | ✅ 无 |
| SQL 注入 | ✅ 无 |
| 非预期二进制 | ✅ 无 |
| LightGBM 结构完整 | ✅ 1,458 行纯文本树结构 |

**结论**: 模型文件为纯数值树结构文本，无恶意代码、无后门、无注入。

---

## 4. 统计有效性认证 ⚠️ PARTIAL (5 PASS / 3 FAIL)

### 4.1 组合层面 ✅ 稳健（非过拟合）

| 检查项 | 结果 | 阈值 |
|---|---|---|
| return | ✅ +16.4% | >0 |
| sharpe | ✅ 1.202 | >0.75 |
| drawdown | ✅ 9.6% | <0.15 |
| trades | ✅ 301 | >60 |
| deployment_status | ✅ DEPLOYABLE_CANDIDATE | — |
| permutation | ✅ p=0.001 | 真实收益 > 随机 95% 分位 |
| walk_forward IR | ✅ 0.926 | >0.5 (3 段: +27.6%/+46.1%/-3.7%) |

### 4.2 单笔层面 ❌ 不显著

| 检查项 | 结果 | 阈值 |
|---|---|---|
| t_test | ❌ p=0.131 (均值 +0.07%) | p<0.05 |
| wilcoxon | ❌ p=0.190 (中位数 0.0) | p<0.05 且 median>0 |
| bootstrap_ci | ❌ 95% CI [-0.0002, +0.0016] 含 0 | 下界>0 |

**结论**: `NOT_SIGNIFICANT` → `NOT_DEPLOYABLE`

---

## 5. 综合认证结论

| 认证维度 | 结果 |
|---|---|
| 完整性 (防篡改/可追溯) | ✅ PASS |
| 鲁棒性 (异常输入) | ✅ PASS |
| 文件安全 (无后门/注入) | ✅ PASS |
| 统计-组合层面 (非过拟合) | ✅ PASS |
| 统计-单笔层面 (边际显著性) | ❌ FAIL |

### 最终判定

**模型"安全"（工程层面）: PASS** —— 无后门、无注入、鲁棒、可复现、可追溯，可安全部署到网关。

**模型"盈利可靠性"（统计层面）: NOT_SIGNIFICANT** —— 组合层面稳健（permutation p=0.001、walk-forward IR 0.926、sharpe 1.2），但单笔收益边际不显著（t-test p=0.13、wilcoxon p=0.19、中位数 0.0）。

这是**数据天花板**（rank_ic 0.044、AUC 0.53）的体现：微弱横截面信号靠组合复利实现正收益，但单笔边际薄弱，达不到"每笔统计显著"的严格标准。经 21 轮训练探索（4 方案 × 多数据源/规模）已充分验证无法突破。

---

## 附: 认证产物

- `models/MODEL_MANIFEST.json` — 模型清单 (checksum + 元数据)
- `quant-local/audit_result.json` — 完整性/鲁棒性/文件安全 22 项结果
- `quant-local/check_result.json` — 统计显著性检测结果
- `quant-local/model_security_audit.py` — 认证脚本 (可复跑)
- `quant-local/run_all_checks.py` — 统计检测入口 (可复跑)
