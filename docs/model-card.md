# 模型卡（量化预测层）

本文件是**模板与版本历史**。每个模型版本落盘时还会生成一份**当版实况**模型卡：

```
artifacts/models/{model_key}/{version}/model_card.md
```

那份是自动渲染的（`services/prediction/training/artifacts.py::render_model_card`），
里面的数字全部来自真实训练产物，不是手填的。**发布前请以那份为准**；本文件只负责
说明口径、限制与版本沿革。

> **仅供研究，不构成投资建议。** 本层输出的是研究性概率与区间，不保证任何收益率。

---

## 1. 目标

| model_key | horizon | 目标定义 | 参考价 |
| --- | --- | --- | --- |
| `a_share_today_lightgbm` | `today_close` | 当日收盘价 / **昨日收盘价** - 1 | **固定为昨收** |
| `a_share_5d_lightgbm` | `next_5d` | **第 5 个后续交易日**收盘价 / 预测参考价 - 1 | 各自 `as_of` 时点最新有效价 |

- 方向标签：目标收益率 **> 0** 记为上涨，否则（含恰好为 0）记为非上涨。
- **上涨概率只来自方向分类模型 + 概率校准器**。不由回归值反推，**更不来自任何大语言模型**（spec §4.2）。
- "第 5 个后续交易日"只有一个实现：`apps/api/app/core/trading_calendar.py::nth_trading_day_after`。
  节假日、跨年一律按交易日历顺延，绝不按自然日计数。
- `today_close` 的参考价固定为昨收，因此同一天 09:45 / 10:00 / … / 14:45 生成的多个版本
  预测的是**同一个目标**，概率变化可以横向比较（spec §7.4）。

## 2. 数据

- 来源：PostgreSQL（`bars` / `documents` / `universe_memberships`），经 OpenBB 网关入库。
  预测层**只读已入库数据**，不直接访问任何外部 URL（模块边界 spec §5.1）。
- 训练前先导出为按 `instrument, datetime` 排序的 **Parquet 快照**，并写 manifest
  （行数、最小/最大时间、**SHA-256**）。产物记录该 manifest，因此"这个模型是用哪份数据训的"可复算。
- Qlib `instruments` 文件由 `universe_memberships` 的**真实** `effective_from` / `effective_to` 生成。
  **禁止用当前 300 只回填历史** —— 那会制造幸存者偏差，让回测结果凭空变好。
- 复权：日线与分钟线必须同一复权基准（默认 `qfq`）。不一致直接 `InsufficientData`，
  绝不静默换算（除权日会算出错误的开盘缺口）。

### point-in-time（第一优先级）

| 数据 | 可见时刻 |
| --- | --- |
| 日线 | **该交易日的收盘 15:00**（不是 `bar_time`） |
| 5 分钟线 | `bar_time`（约定为该 bar 的**结束**时刻） |
| 文档（公告/新闻） | `published_at`（**不是** `observed_at`） |

日线那一行是最关键的：上游常把日线的 `bar_time` 写成当日 **00:00**。若按 `bar_time <= cutoff`
判定可见性，09:45 的今日预测就会读到**当天的收盘价** —— 也就是它要预测的答案。
可见性因此由**交易日收盘时刻**决定，实现在 `services/prediction/features/pit.py`。

三道防线：SQL WHERE → 可见性过滤 → `PitPanel` 构造断言（越界即 `PitViolation`，进程炸掉）。

## 3. 特征

定义在 **`config/features/{version}.yaml`**（当前 `v1`，38 个特征）。

| 组 | 特征 |
| --- | --- |
| 动量 | `ret_1/2/5/10/20/60` |
| 趋势 | `ma_dist_5/10/20/60` |
| 波动 | `vol_5`、`vol_20`、`atr_14`、`amplitude_1` |
| 成交 | `volume_rel_ma20`、`turnover_rate`、`volume_ratio` |
| 市场 | `bench_csi300_ret_1/5/20`、`bench_sse_ret_1/5/20`、`rel_strength_{csi300,sse}_1/5/20` |
| 事件 | `doc_count_1d`、`doc_count_5d`、`announcement_count_5d`、`news_count_5d`、`hours_since_last_document` |
| 今日模型专用 | `open_gap`、`ret_since_0945`、`morning_range`、`morning_volume_share` |

- `next_5d` 只用 base（34 个）；`today_close` 用 base + intraday（38 个）。
- **任何字段、窗口或缺失值策略的变化都必须升级版本号**（新建 `v2.yaml`，不得原地改 `v1`）。
  这条是**机器强制**的：产物记录特征集的 sha256，推理时若 `config/features/v1.yaml` 内容变了，
  哈希对不上 → 直接 `ModelUnavailable`（`inference/loader.py`）。
- 缺失一律用 `None` 表示，按 yaml 的 `missing` 策略进入模型（`nan` / `zero`）。
  **绝不用 0 冒充缺失** —— 0 在收益率里是一个有意义的取值。

### 当前数据口径下恒为缺失的特征

| 特征 | 原因 |
| --- | --- |
| `turnover_rate` | 换手率 = 成交量 / 自由流通股本，而 spec §6 的 `instruments` / `bars` 表**没有股本字段**。宁可少一个特征，也不用"成交额/收盘价"之类的东西冒充。数据口径补上 `free_float_shares` 后，无需改特征定义即可生效。 |
| `ret_since_0945` / `morning_range` / `morning_volume_share` | 仅当历史分钟线可得时才有值。分钟线回补不足时，今日模型**退化为开盘模型**（只保留 `open_gap`），记录 `minute_bars_insufficient` 并**强制置信度为 `low`**。 |

训练时全缺失的列会被自动识别，写入 `feature_schema.json` 的 `unavailable_features` 与当版模型卡。

## 4. 训练与验证

```
全部交易日
  ├─ dev  = 前 80%
  │    └─ expanding-window walk-forward（每折在 fold.train 训练，在 fold.validation 预测）
  │         → 汇总样本外验证预测（= spec §9.4 的"验证覆盖"）
  │         → 概率校准器在这批样本外预测上拟合
  │         → 归一化参数只在 dev 上拟合（供历史相似行情用）
  ├─ embargo（禁运期 = horizon 的交易日数：next_5d=5，today_close=1）
  └─ test = 后 20%，**只**用来跟基准比，不参与任何拟合
```

- **禁止随机切分**（spec §9.3）。任何时间上交错的切分都会被 `training/splits.py::assert_time_ordered` 判死。
- **禁运期**是最容易被忽略的一处泄漏：`next_5d` 的标签要看未来 5 个交易日，
  若训练段末尾紧挨着验证段开头，训练标签就已经"看过"验证期的价格。
- 最终模型在整个 dev 上重训，轮数取各折早停轮数的中位数 —— **测试段全程不参与任何选择**。
- 四个 booster：`regressor`（期望收益）、`classifier`（**上涨概率**）、`q20` / `q80`（区间）。

### 概率校准

- 时间验证集上做 **isotonic regression**。
- 验证样本 **< 200** → 降级为 **Platt scaling**，并在模型卡中记录降级（spec §9.3.1）。
- 验证集只有单一方向标签 → 保持恒等映射并标记降级（拟合噪声毫无意义）。
- 校准器序列化为 **JSON**（isotonic 存断点 / Platt 存系数），不是 pickle：
  推理侧因此只需要 `math`，不依赖 sklearn 版本。
- **"校准合格"的定义**（spec §9.5 用到但未定义，本项目据此固定）：
  `ECE <= 0.10` **且** 校准后 Brier 不劣于校准前。

## 5. 基准与发布门槛

三个基准（spec §9.3）：

| 基准 | 定义 | 进发布门槛？ |
| --- | --- | --- |
| 恒定上涨概率 | **训练窗口**的上涨频率作为常数概率 | ✅ |
| 历史均值收益 | **训练窗口**的平均收益作为常数预测 | ✅ |
| 沪深300 方向 | 参照量 | ❌ |

> 沪深300 方向基准刻意**不进**门槛：它用的是同期**已实现**的大盘方向，事前根本拿不到。
> 把它当作"可击败的对手"会误导人。它只作为"跟着大盘猜能对多少"的参照出现。

```
better_than_baseline = (Brier < 恒定概率基准) AND (MAE < 历史均值基准)
```

**两个都必须严格优于，且在同一个测试窗口上比**（spec §9.3.1）。任一指标缺失或非有限 → `false`（fail closed）。

### 发布门槛（spec §9.4）

候选成为可运行的 `active` 版本必须**同时**满足：

1. 无未来数据泄漏测试失败（`leakage_tests_passed` 必须由跑过测试的流水线**显式**传入；默认 `False`）；
2. 验证覆盖 ≥ **120** 个日预测 或 **60** 个周预测；
3. Brier、MAE、方向准确率及对应基准**均已计算且为有限数值**；
4. 区间覆盖率、方向准确率、样本数完整可见。

- **不要求模型达到预设收益率。**
- **未优于基准的模型仍可成为 `active` 研究模型**，但 `better_than_baseline=false`，
  置信度**只能是 `low`**，前端必须标记「未优于基准」。
- **`candidate` 状态永远不对 API 提供预测。** 未过门槛的候选无法被激活（`training/registry.py::activate` 直接拒绝）。

## 6. 置信度（spec §9.5）

| 档 | 条件 |
| --- | --- |
| `high` | `better_than_baseline=true` 且 验证样本 ≥ **2×** 最低门槛 且 校准合格 且 所有关键特征 **PSI ≤ 0.10** |
| `medium` | `better_than_baseline=true` 且 ≥ 最低门槛 且 校准合格 且 所有关键特征 **PSI ≤ 0.20** |
| `low` | `better_than_baseline=false` 或 数据降级 或 任一关键特征 **PSI > 0.20** |

补充两条本项目的 fail-closed 约定：

- **PSI 未知**（漂移监控尚未产出报告）→ 最多 `medium`。
  `high` 要求"所有关键特征 PSI ≤ 0.10"，没有证据就不能声称满足；但未知也不构成"漂移了"的证据，所以不压到 `low`。
- 低于最低样本门槛的模型**不得成为 active**（这是发布门槛，不是降级项）。

## 7. 漂移监控（PSI）

- 每日计算（`services/worker/jobs/prediction_jobs.py::compute_feature_drift`）。
- 参考分布来自**训练窗口**，训练时算好写入产物 `psi_reference.json`。
- 线上分布取自**最近 20 个交易日实际生成的预测**的 `features_snapshot` —— 也就是模型真正吃进去的那批特征。
- 分箱含一个 **missing 桶**：某个特征突然全变 NaN 是最该报警的一类漂移，只对非空值分箱会完全看不见。
- 阈值：**> 0.20** → 标记漂移，置信度压到 `low`；**> 0.30** → **停止生成新预测**，返回 `MODEL_UNAVAILABLE`。
- 没有线上样本时**不写报告**（没有证据 ≠ 没有漂移）。

## 8. 结算与成绩单

- `today_close`：当日收盘数据确认后结算。
- `next_5d`：第 5 个后续交易日收盘后结算（目标日在生成预测时就用交易日历算好写进 `target_at`）。
- **幂等**；**预测账本不可覆盖**（只 INSERT `prediction_outcomes`，DB 触发器亦挡 `predictions` 的 UPDATE）。

**复权边界**：实际收益必须与参考价算在同一个复权基准上。预测写入时记录了复权锚点
（`anchor_session` / `anchor_close_at_as_of`），结算时用"现在读到的锚点收盘价 / 当时的锚点收盘价"
求出缩放因子，把参考价搬到当前基准上再算收益。期间除权多少次都不影响结果 ——
否则一次 10 送 10 会让收益率凭空多出 -50%。

成绩单计数口径（spec §7.4，容易做错也容易骗人）：

```
eligible_count = 目标时间已到的预测数
settled_count + pending_count = eligible_count
尚未到目标时间的预测不进入分母
```

窗口 20 / 100 / all 作用在按 `as_of` 倒序的 eligible 预测上。指标只在 settled 上计算；
**settled 为 0 时全部返回 `null`，不是 0**。

## 9. 限制

- 本层是**研究工具**，不构成投资建议，不保证任何收益率。
- **`next_5d` 的训练/线上参考价锚点不一致**（spec 层面的固有张力，如实记录）：
  训练样本以交易日收盘（15:00）为 cutoff，参考价 = 当日收盘价；
  而线上 09:45 / 11:30 生成的 `next_5d` 预测，参考价是「`as_of` 时点最新有效价」（spec §7.4 明文要求）。
  因此盘中版本的预期收益，本质是"收盘到收盘的 5 日收益"被套用在一个盘中锚点上，
  两者相差一个当日盘中波动。**15:20 的版本没有这个问题。**
  预测记录里带 `reference.intraday_anchor=true` 标记，可据此筛查。
- 数据不足一律 `InsufficientData` / `ModelUnavailable` **fail closed**，
  绝不返回默认值或假概率：日线 < 3 年不启用一周模型；今日模型 < 120 个有效交易日不启用；
  早盘分钟特征不足退化为开盘模型并标记原因。
- 历史相似行情（spec §10）：有效候选 **< 30** 时**关闭功能**并说明样本不足；
  相似案例**不得**被描述为因果关系。

## 10. 复现

每个模型版本的 `provenance.json` 记录：

- 代码 commit SHA
- 依赖锁文件哈希
- 特征配置 sha256 / 模型配置 sha256
- 数据快照 id 与各文件 SHA-256
- 训练 / 验证 / 测试的时间范围

产物目录写完即**只读**（`0o444` / `0o555`）：产物不可变，改模型只能出新版本。

`artifact_uri` 形如 `file:///models/{model_key}/{version}`（容器内只读挂载）。

---

## 版本历史

| 日期 | 特征集 | 模型版本 | 说明 |
| --- | --- | --- | --- |
| 2026-07-14 | `v1` | — | 量化预测层建成：PIT 特征（38 个）、Qlib 数据契约、walk-forward + LightGBM（回归 + 方向）、isotonic/Platt 校准、发布门槛、置信度、PSI 漂移、结算与成绩单、历史相似行情。**尚无在真实 A 股数据上训练出的已发布模型版本** —— 首个候选需在真实数据回补完成后产出，并通过发布门槛后方可激活。 |

> 每训练出一个新版本，在此追加一行；当版实况见
> `artifacts/models/{model_key}/{version}/model_card.md`。
