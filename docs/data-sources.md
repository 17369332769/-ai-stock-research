# 数据来源登记

> **发布检查依赖本文件**：spec §5.2 明确「实现前必须在 `docs/data-sources.md` 记录每个上游来源的
> 使用条款、访问日期、频率限制和延迟说明；**缺少该记录时发布检查失败**」。
> 本文件同时是参数映射、返回列、Provider 命令、样例响应与 OpenBB REST 路由的**唯一登记处**，
> 并由 `services/*/tests/` 的契约测试锁定 —— 改代码不改本文件（或反之）会让契约测试变红。

- 文档版本：0.1.0（与 `pyproject.toml` 的 `version` 同步）
- 最后更新：2026-07-14
- 适用范围：A 股 AI 研究助手 MVP，沪深300 股票池

---

## 0. 许可与合规（必读）

| 项目 | 结论 |
|---|---|
| **数据用途** | **仅限个人研究**。三个上游都是免费公开数据，**不得重新分发**（spec §5.2），不得对外提供数据服务、不得二次销售、不得公开托管带数据的实例。 |
| **实时性** | 免费数据源**不保证交易所级实时性**（spec §21）。东方财富快照有秒级到分钟级延迟，且无 SLA。产品界面必须显示 `freshness` 与 `age_seconds`，禁止把 stale 行情当实时展示。 |
| **OpenBB 许可证** | AGPL-3.0。MVP 只允许本地个人使用，**不得公开托管**；公开或商业发布前必须单独完成许可证与底层数据授权审查（spec §4.2）。 |
| **本项目许可证** | AGPL-3.0-only（见 `pyproject.toml`），与 OpenBB 一致。 |
| **投资建议** | 全部输出仅供研究，不构成投资建议（`RESEARCH_ONLY_DISCLAIMER`）。 |

**访问日期声明**：下表所有「访问日期」标注为 `未验证`，因为本次实现环境**无外网访问**。
所有上游 URL、字段名与频率限制来自 akshare 1.18.64 的接口定义与各平台公开文档，
**未经真实抓取验证**。首次联网部署时必须逐个复核并回填本表 —— 详见 §6「未验证事项」。

---

## 1. 架构约束：谁可以碰第三方 URL

```
        ┌─────────────────────── 只有这一层能碰第三方 ───────────────────────┐
AKShare ─┐                                                                  │
巨潮资讯 ─┼─> services/openbb_extensions/{akshare,cn_disclosure,csi300}_provider │
中证指数 ─┘                              │                                   │
        └─────────────────────────────── │ ──────────────────────────────────┘
                                         v
                                OpenBB 内部 REST (:6900)
                                         │
                       services/market_data/openbb_gateway.py   ← 业务代码的唯一出口
                                         │
                    normalization → ingest → PostgreSQL → 预测 / Agent / API
```

- 业务代码（`apps/api`、`services/prediction`、`services/research`、`services/worker`）
  **不得** import akshare、不得直接 httpx 打第三方 URL。
- 违规由 `apps/api/tests/integration/test_no_direct_third_party_calls.py` 断言（验收 §15.19）。
- **不做静默备用源**（spec §5.2）：主源失败 → `ProviderUnavailable`（HTTP 424）→ UI 显示
  stale/unavailable。**绝不**用另一个源的数据顶替，避免不同口径混合。

---

## 2. AKShare Provider（行情 / 日线 / 5 分钟线 / 新闻）

| 项 | 值 |
|---|---|
| Provider 名 | `akshare` |
| 包 | `services/openbb_extensions/akshare_provider/`（发行名 `openbb-akshare`，模块 `openbb_akshare`）|
| 上游平台 | 东方财富（quote.eastmoney.com），经 `akshare==1.18.64` 抓取 |
| `source` 字段值 | `eastmoney_via_akshare`（与 spec §7.2 响应样例一致）|
| 使用条款 | 东方财富公开页面数据；akshare 为 MIT 许可的抓取客户端。免费、无官方 SLA、**不得重新分发** |
| 访问日期 | **未验证**（本次实现无外网）|
| 频率限制 | 上游无公开配额；实测高频访问会被限流/封 IP。本项目节流：报价 15s/次（全市场快照单次请求覆盖所有自选股），K 线按标的串行 |
| 延迟 | 快照通常 3–15 秒延迟；**不是交易所级实时**。`stock_zh_a_spot_em` **不返回时间戳**，`observed_at` = 我们的取数时刻（`Clock.now()`），不冒充撮合时间 |
| 复权 | 日线/分钟线固定 **前复权 `qfq`**。`bars.adjustment` 落 `qfq`，同表禁止混复权口径 |
| 单位 | 成交量 = **手**（1 手 = 100 股）；成交额 = **元**。spot / daily / 5m 三接口口径一致 |

### 2.1 允许调用的函数（spec §5.2 硬约束，运行时白名单强制）

只允许这 4 个，白名单在 `akshare_provider/client.py:ALLOWED_AKSHARE_FUNCTIONS`，
越权调用抛 `ProviderConfigError`，由 `test_akshare_allowlist.py` 锁死：

| akshare 函数 | 用途 | 参数 |
|---|---|---|
| `stock_zh_a_spot_em()` | 全市场实时快照 | 无参数（整表拉取后本地按 symbol 过滤）|
| `stock_zh_a_hist()` | 日线 | `symbol`, `period="daily"`, `start_date=YYYYMMDD`, `end_date=YYYYMMDD`, `adjust="qfq"` |
| `stock_zh_a_hist_min_em()` | 5 分钟线 | `symbol`, `start_date="YYYY-MM-DD HH:MM:SS"`, `end_date=...`, `period="5"`, `adjust="qfq"` |
| `stock_news_em()` | 个股新闻 | `symbol`（**不接受时间窗**，只返回最近约 100 条）|

### 2.2 返回列 → OpenBB Data 字段映射

**`stock_zh_a_spot_em` → `EquityQuote`**（`akshare_provider/transform.py:transform_spot`）

| 上游列（中文）| OpenBB 字段 | 落库列 | 备注 |
|---|---|---|---|
| 代码 | `symbol` | `quotes.symbol` | 归一化去 `sh`/`sz` 前缀 |
| 名称 | `name` | `instruments.name` | |
| 最新价 | `last_price` | `quotes.price` | **必填**，缺失/非数值 → fail closed |
| 昨收 | `prev_close` | `quotes.previous_close` | **必填**；`today_close` 目标的参考价 |
| 今开 | `open` | `quotes.open` | |
| 最高 / 最低 | `high` / `low` | `quotes.high` / `low` | |
| 成交量 | `volume` | `quotes.volume` | 单位：手 |
| 成交额 | `turnover` | `quotes.amount` | 单位：元 |
| 量比 | `volume_ratio` | `quotes.volume_ratio` | |
| 涨跌幅 | `change_percent` | （只进 `raw_payload`）| 展示用涨跌幅由 `price/previous_close-1` 计算，不信上游 |
| 换手率 | `turnover_rate` | （只进 `raw_payload`）| |
| —（无此列）| `last_timestamp` | `quotes.observed_at` | = `Clock.now()`，见上文延迟说明 |

**`stock_zh_a_hist` / `stock_zh_a_hist_min_em` → `EquityHistorical`**

| 上游列 | OpenBB 字段 | 落库列 | 备注 |
|---|---|---|---|
| 日期 / 时间 | `date` | `bars.bar_time` | 见 §2.3 时间语义 |
| 开盘 | `open` | `bars.open` | ⚠️ 上游列序是 **开盘/收盘/最高/最低**，不是 OHLC |
| 收盘 | `close` | `bars.close` | |
| 最高 / 最低 | `high` / `low` | `bars.high` / `low` | |
| 成交量 | `volume` | `bars.volume` | 单位：手 |
| 成交额 | `turnover` | `bars.amount` | 单位：元 |
| 换手率 | `turnover_rate` | —（不落库）| |

**`stock_news_em` → `CompanyNews`**

| 上游列 | OpenBB 字段 | 落库列 |
|---|---|---|
| 新闻标题 | `title` | `documents.title` |
| 新闻内容 | `text` | `documents.body_text` |
| 发布时间 | `date` | `documents.published_at` |
| 新闻链接 | `url` | `documents.source_url` |
| 文章来源 | `source` | `documents.source` |
| 关键词 | `keyword` | —（不落库）|

### 2.3 时间语义（**决定有没有数据泄漏**）

| K 线 | `bar_time` 取值 | 理由 |
|---|---|---|
| 日线 `1d` | 该交易日 **15:00**（收盘时刻）| 日线在收盘前不可知。若落 00:00，「当日 09:45 的特征」会取到当日日线 → 未来数据泄漏。落 15:00 后 `bar_time <= data_cutoff` 天然等价于「这根 K 线已经走完」 |
| 5 分钟 `5m` | 该 K 线的**结束时刻** | `09:35` 覆盖 09:30–09:35 的成交。用结束时刻，`bar_time <= data_cutoff` 才等价于「这根 K 线在 cutoff 时已走完」 |

盘前集合竞价行（≤09:30）与盘后越界行（>15:00）在 transform 层丢弃。

### 2.4 OpenBB REST 路由

```
GET /api/v1/equity/price/quote?provider=akshare&symbol=600519,000001
GET /api/v1/equity/price/historical?provider=akshare&symbol=600519&interval=1d&start_date=2026-07-01&end_date=2026-07-14&adjustment=qfq
GET /api/v1/news/company?provider=akshare&symbol=600519&start_date=2026-07-01&end_date=2026-07-14
```

### 2.5 样例响应

见 `apps/api/tests/fixtures/providers/`：
`akshare_spot_em_raw.json`、`akshare_hist_daily_raw.json`、`akshare_hist_min_raw.json`、
`akshare_news_em_raw.json`（上游原始，脱敏）；
`openbb_quote_ok.json`、`openbb_bars_1d_ok.json`、`openbb_bars_5m_ok.json`、`openbb_news_ok.json`
（OpenBB REST 出参）。

### 2.6 降级策略

| 故障 | 行为 |
|---|---|
| HTTP 429 / 5xx / 超时（30s）/ 网络错误 | `ProviderUnavailable`（424）。**不切源、不返回缓存** |
| 字段缺失 / 类型改变 / 非有限数值 | `ProviderUnavailable` + 明确指出是哪个字段。**绝不用 0 或上一条填** |
| 脏数据（负价、`prev_close=0`、high<low）| 网关原样透出 → `normalization.validate_*` **逐条拒收**并记入 `IngestReport.rejected` → 写进 `jobs.warnings` 与日志。整批全脏 → `ProviderUnavailable` |
| 上游未返回某个自选股 | 不补零、不复制上一条；该标的自然进入 stale → 超过 `QUOTE_STALE_SECONDS`(180s) 显示 stale，从未取得则 API 返回 424 |
| 5 分钟线不可得（新股/停牌/超出上游保留窗口）| **回补作业只记 warning，不使整项回补失败**（spec §7.1）|
| 连续失败 3 次 | 数据源进入**降级状态**，`get_source_health()` 暴露失败源 + 最后成功时间（spec §8）|

---

## 3. 中国法定披露 Provider（公告）

| 项 | 值 |
|---|---|
| Provider 名 | `cn_disclosure` |
| 包 | `services/openbb_extensions/cn_disclosure_provider/`（发行名 `openbb-cn-disclosure`）|
| 权威来源 | **巨潮资讯 cninfo.com.cn**（中国证监会指定信息披露平台）|
| `source` 字段值 | `cninfo` |
| 来源白名单 | `{cninfo, sse, szse}` —— spec §5.2：**只返回巨潮/上交所/深交所原文**。媒体转载稿不得从这里出去（转载属于「新闻」，走 akshare Provider）|
| 使用条款 | 公开披露信息，免费查阅；**不得重新分发**、不得批量转售 |
| 访问日期 | **未验证**（本次实现无外网）|
| 频率限制 | 无公开配额；实测高频访问返回 403 / 验证码。本项目节流：翻页间隔 0.5s，单次查询最多 20 页 × 30 条 |
| 延迟 | 公司披露即时可见；巨潮索引通常分钟级 |

### 3.1 上游接口

| 步骤 | 方法 | URL | 表单参数 |
|---|---|---|---|
| 1. 取 orgId | POST | `http://www.cninfo.com.cn/new/information/topSearch/query` | `keyWord=600519`, `maxNum=10` |
| 2. 查公告 | POST | `http://www.cninfo.com.cn/new/hisAnnouncement/query` | `stock=600519,gssh0600519`, `column=sse\|szse`, `tabName=fulltext`, `seDate=2026-07-10~2026-07-14`, `pageNum`, `pageSize=30`, `isHLtitle=true` |
| 3. 原文 PDF | GET | `http://static.cninfo.com.cn/{adjunctUrl}` | — |

`column` 路由：`6`/`9` 开头 → `sse`；`0`/`2`/`3` 开头 → `szse`。
必需请求头：`Referer`、`X-Requested-With: XMLHttpRequest`、桌面 UA（缺失会被拒）。

### 3.2 返回字段映射

| 上游字段 | OpenBB 字段 | 落库列 | 备注 |
|---|---|---|---|
| `announcementTitle` | `title` | `documents.title` | **必填** |
| `announcementTime` | `date` | `documents.published_at` | epoch **毫秒** → `Asia/Shanghai` |
| `adjunctUrl` | `url` | `documents.source_url` | 拼 `static.cninfo.com.cn` → **原文 PDF** |
| `secCode` | `symbols` | `documents.symbol` | 与请求标的不符的记录剔除（同 orgId 下可能混入 B 股/债券）|
| — | `text` | `documents.body_text` | **恒为 `NULL`**，见下 |
| `announcementId` / `orgId` / `announcementType` | 同名扩展字段 | —（不落库）| |

**⚠️ 已知能力边界：`body_text` 恒为 `NULL`。**
公告原文是 PDF，MVP **不做 PDF 解析**（未引入解析依赖）。这不是「静默丢数据」，是明确的能力边界：
- 下游 Agent 只能引用**标题**与**原文链接**作为证据，不得凭空生成正文内容（spec §11.3）。
- spec §7.3 要求 `evidence[].quote` 是原文中连续存在的 1–300 字符 —— 对公告而言，
  当前只能从**标题**取 quote。若产品要求引用正文，必须先引入 PDF 解析（后续版本）。
- **这是一个必须让产品/Agent 负责人知晓的限制，不是实现细节。**

### 3.3 OpenBB REST 路由

```
GET /api/v1/news/company?provider=cn_disclosure&symbol=600519&start_date=2026-07-10&end_date=2026-07-14
```

**为什么公告复用 `CompanyNews` 标准模型**：OpenBB 自定义**路由**需要另一组 `openbb_core_extension`
entry point 并重建路由表；而公告的字段形态（标题/正文/时间/原文链接/关联证券）与 `CompanyNews`
完全同构。**「公告 vs 新闻」由 provider 区分**：

| provider | `documents.document_type` | 含义 |
|---|---|---|
| `cn_disclosure` | `announcement` | 法定披露原文 |
| `akshare` | `news` | 媒体报道 |

网关据此写入 `document_type`，两种口径不会混淆。

### 3.4 未接线的备用入口（**不得作为静默 fallback**）

| 来源 | URL | 状态 |
|---|---|---|
| 上交所 | `http://www.sse.com.cn/disclosure/listedinfo/announcement/` | 登记备查，**未接线** |
| 深交所 | `https://www.szse.cn/disclosure/listed/notice/index.html` | 登记备查，**未接线** |

巨潮同时覆盖沪深两市且是法定披露平台，因此**不需要**在三者之间做静默切换。
spec §5.2 明令禁止静默备用源：**巨潮失败即 unavailable**。

### 3.5 去重

- **公告按 `content_hash` 去重**：`sha256(document_type | symbol | title | body_text)`。
  **不含 URL** —— 同一份公告可能在不同板块页以不同 URL 挂出。
- 标题做 NFKC 归一化 + 空白折叠：上游换排版不该产生「新公告」。
- `documents.content_hash` 有 UNIQUE 约束 → DB 层幂等兜底。

### 3.6 降级策略

同 §2.6（429/5xx/超时/非 JSON/字段缺失 → `ProviderUnavailable`；连续失败 3 次降级）。
上游对「该窗口无公告」返回 `announcements: null` —— 这是**合法空值**，返回 `[]`，不是错误。

---

## 4. CSI300 Provider（沪深300 成分）

| 项 | 值 |
|---|---|
| Provider 名 | `csi300` |
| 包 | `services/openbb_extensions/csi300_provider/`（发行名 `openbb-csi300`）|
| 权威来源 | **中证指数有限公司**（csindex.com.cn）官方成分文件与指数调整公告（spec §4.1）|
| `source` 字段值 | `csindex` |
| 使用条款 | 中证指数公开发布的成分数据，免费查阅；**不得重新分发** |
| 访问日期 | **未验证**（本次实现无外网）|
| 频率限制 | 无公开配额。本项目每交易日只抓 2 次（07:30 / 18:30）|
| 延迟 | 成分只在**定期调整日**变化（通常每年 6 月/12 月第 2 个星期五后的下一交易日），日常无变化 |

### 4.1 上游接口

```
GET https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/000300cons.xls        （主）
GET https://csi-web-dev.oss-cn-shanghai-finance-1-pub.aliyuncs.com/.../000300cons.xls                        （OSS 镜像）
```

两个地址是**同一发行方的主站与 OSS 镜像**，口径完全一致 —— 在它们之间重试**不违反**
「不做静默备用源」（那条规则禁止的是**跨发行方/跨口径**顶替）。

指数调整公告（人工核对入口，MVP 不自动解析 PDF）：`https://www.csindex.com.cn/#/about-us/notice`

### 4.2 文件格式与解析

中证历史上投递过多种格式，因此解析器做**显式格式探测**（`csi300_provider/transform.py`）：

1. 制表符/逗号分隔文本（GBK / GB18030 / UTF-8）
2. HTML `<table>`
3. 真 Excel（BIFF `.xls` / `.xlsx`）→ 走 `pandas.read_excel`

三种都不匹配 → `ProviderDataError`（fail closed）。

**列名按语义匹配，不按列序号**（中证调整过列顺序）：

| 语义 | 候选表头关键字（**按优先级从具体到通用**）|
|---|---|
| 成分代码 | `成分券代码` / `Constituent Code` / `证券代码` / `股票代码` / `code` |
| 成分名称 | `成分券名称` / `Constituent Name` / `证券名称` / `股票名称` / `name` |
| 交易所 | `交易所` / `Exchange` |
| 生效日期 | `日期` / `Date` |

⚠️ **优先级不是可选项**：官方文件里同时存在「指数代码 Index Code」和「成分券代码 Constituent Code」。
若先用通用词 `code` 匹配，会命中**指数代码列**，300 只成分全部变成 `000300`。
因此通用词匹配时排除 `指数代码/指数名称/英文名称` 列（`INDEX_OWN_COLUMNS`）。

### 4.3 守门规则（防止一次坏抓取摧毁历史）

| 规则 | 行为 |
|---|---|
| 解析出的成分 < **250** 只 | `ProviderDataError`。半截文件若被接受，成分同步会把几十只股票误标为「已调出」|
| 空成分列表 | 网关层 `ProviderUnavailable`。**空不是「今天没有成分股」，是上游坏了** |
| 交易所列与代码前缀矛盾 | `ProviderDataError`（不二选一猜）|
| 文件名日期 ≠ 文件内日期（历史快照）| `ProviderDataError`（归档被污染）|

### 4.4 当前成分 vs 历史成分（**幸存者偏差防线**）

spec §9.3：训练样本按每个交易日**当时有效**的 CSI300 历史成员生成，
**禁止用当前 300 只股票回填全部历史**。因此有两条语义不同的路径，**绝不互相顶替**：

| `as_of` | 数据来源 | 用途 |
|---|---|---|
| `>= 今天`（或缺省）| 中证官网**当期成分文件** | 选股（`/universes/CSI300/instruments`、搜索、自选股成员校验）|
| `< 今天` | **官方历史成分快照归档** | 训练取样、回测 |

**快照缺失 → `SnapshotNotFound`（fail closed）。绝不回退到当前成分。**
若回退，2024 年的训练样本就会包含 2026 年才调入的股票 —— 回测会好看得离谱且完全不可信。
由 `test_csi300.py::test_missing_historical_snapshot_fails_closed_not_current` 锁死。

### 4.5 历史快照归档

| 项 | 值 |
|---|---|
| 目录 | `$CSI300_SNAPSHOT_DIR`（默认 `data/csi300_snapshots`）|
| 文件名 | `000300cons_YYYYMMDD.{csv,tsv,txt,xls,xlsx,html}`，`YYYYMMDD` = **该成分表的官方生效日期** |
| 写入方式 | **write-through**：每次成功抓取当期成分自动存档一份原始 bytes（`$CSI300_SNAPSHOT_ARCHIVE=0` 可关闭）|
| 查询语义 | 返回「生效日期 `<=` as_of 的**最新一份**」；记录带 `snapshot_date`，调用方能看到它与 `as_of` 的差距 |

**⚠️ 归档稀疏时的风险（必须知晓）**：
「取 `<=` as_of 的最新快照」是成分这种**阶梯函数**的正确 point-in-time 语义 ——
**前提是归档没有漏掉中间的调整日**。

- 系统上线**之后**：每交易日 2 次抓取 → 归档稠密，不会漏调整日。
- 系统上线**之前**的历史：归档为空。要做无偏历史训练，**必须人工从中证官网补齐**
  各调整日的官方成分文件到归档目录。
- **在补齐之前，任何早于上线日的训练样本都拿不到成分快照，会直接报错** —— 这是刻意的：
  宁可训练跑不起来，也不能让幸存者偏差悄悄进入模型。

### 4.6 成分有效期差分（`universe_memberships`）

Provider 只返回**某一天的快照**，它不知道历史。有效期由 `ingest.sync_universe_members` 差分计算：

| 情况 | 行为 |
|---|---|
| 快照里有、库里无未闭合区间 | 插入 `[effective_from, NULL)`，`effective_from` = 成分表官方生效日 |
| 库里未闭合、快照里没有 | 闭合 `effective_to` = `as_of` 的**上一个交易日** |
| 已闭合的历史区间 | **永不改写**（spec §8：不覆盖历史有效期）|
| 成分同步失败 | 不写入任何东西 → 库里保持上一份有效期不变，并标记同步失败 |

### 4.7 OpenBB REST 路由

```
GET /api/v1/index/constituents?provider=csi300&symbol=000300&as_of=2026-07-14
```

`as_of` 是 provider 特有参数（标准模型没有）。

### 4.8 样例响应

`apps/api/tests/fixtures/providers/csindex_cons_tsv.txt`（GBK 制表符，300 只）、
`csindex_cons_table.html`（HTML 表格）、`openbb_constituents_ok.json`（OpenBB REST 出参）。

---

## 5. OpenBB REST 路由总表（网关唯一出口）

| 网关方法 | HTTP | 路由 | provider | 关键参数 |
|---|---|---|---|---|
| `get_universe_members` | GET | `/api/v1/index/constituents` | `csi300` | `symbol=000300`, `as_of` |
| `search_instruments` | GET | `/api/v1/index/constituents` | `csi300` | 同上（在网关内做匹配与排序）|
| `list_instruments`\* | GET | `/api/v1/index/constituents` | `csi300` | 同上 |
| `get_quotes` | GET | `/api/v1/equity/price/quote` | `akshare` | `symbol=600519,000001` |
| `get_bars` | GET | `/api/v1/equity/price/historical` | `akshare` | `symbol`, `interval=1d\|5m`, `start_date`, `end_date`, `adjustment=qfq` |
| `get_announcements` | GET | `/api/v1/news/company` | `cn_disclosure` | `symbol`, `start_date`, `end_date` |
| `get_news` | GET | `/api/v1/news/company` | `akshare` | `symbol`, `start_date`, `end_date` |

\* `list_instruments` 是 `OpenBBGateway` Protocol 之外的补充方法，供成分同步作业写 `instruments` 表。

**错误映射**（`services/market_data/openbb_gateway.py`）：

| 上游 | 网关抛出 | HTTP |
|---|---|---|
| 超时 / 网络错误 / 429 / 5xx / 非 JSON / 缺 `results` / 字段缺失 / 类型改变 | `ProviderUnavailable` | 424 |
| OpenBB 返回 400 / 422（**我们传错参数**）| `InvalidArgument` | 400 |
| CSI300 成分为空 | `ProviderUnavailable` | 424 |

**naive datetime 的处理**：OpenBB 序列化时可能丢时区。网关把 naive 值按 **Asia/Shanghai** 解释
（本项目所有上游都是中国境内数据源）。纯日期（`2026-07-14`）补 **15:00**。这是**显式假设**，
不是猜 —— 若将来接入非中国数据源，此处必须改。

---

## 6. 未验证事项（**发布前必须逐条消除**）

本次实现环境 **无外网访问**，且 **`.venv` 依赖安装失败**（见 §6.0），因此
`openbb` / `akshare` / `respx` / `pydantic` **一个都装不上**。以下内容**未经实机验证**，诚实登记如下。

### 6.0 🔴 阻塞项：依赖解析失败（`ResolutionImpossible`）

`pip install -e ".[dev]"` **失败**，实测冲突：

```
ai-stock-research[dev] 0.1.0 depends on ruff==0.8.6 ; extra == "dev"
openbb-core 1.6.10..1.6.13 depends on ruff<0.16 and >=0.15
ERROR: ResolutionImpossible
```

由此实测确认两条**与 spec 不符的硬事实**：

1. **`openbb==4.7.2` 实际要求 `openbb-core>=1.6.10,<2.0.0`** —— 不是 spec §4.1 暗示的 `1.4.10`。
   三个 Provider 包的 `pyproject.toml` 已按实测值改为 `openbb-core>=1.6.10,<2.0.0`。
2. **`openbb-core` 把 `ruff` 当作运行时依赖**，且要求 `ruff>=0.15,<0.16`，
   与根 `pyproject.toml` 的 `dev` extra 中 `ruff==0.8.6` **直接冲突**。

**修复（不在数据层文件所有权内，需骨架/依赖负责人拍板）**：
把 `dev` extra 的 `ruff==0.8.6` 放宽为 `ruff>=0.15,<0.16`。
`[tool.ruff]` 的 `line-length = 110` 与所选规则集在 0.15 上仍然有效。

**在此项修复前，本层的网关/规范化/入库/作业代码一行都没能真正执行过。**

| # | 未验证项 | 风险 | 消除方式 |
|---|---|---|---|
| 1 | **OpenBB entry point 注册** | 三个 Provider 的 `[project.entry-points."openbb_provider_extension"]` 是否被 OpenBB 4.7.2 正确发现 | `pip install -e services/openbb_extensions/{akshare,cn_disclosure,csi300}_provider` 后跑 `python -c "from openbb import obb; print(obb.coverage.providers)"`，确认三个 provider 出现 |
| 2 | **OpenBB 标准模型字段名** | `EquityQuoteData` / `EquityHistoricalData` / `CompanyNewsData` / `IndexConstituentsData` 的**必填字段集**是按 OpenBB 文档推断的，未实机 import 校验。**尤其是 openbb-core 已从 1.4.x 跳到 1.6.x，标准模型可能已变**。若某个必填字段名不同（如 `prev_close` vs `previous_close`），Fetcher 的 `transform_data` 会在运行时报错 | 跑 `services/openbb_extensions/tests/test_provider_registration.py`（当前因缺 `openbb_core` 而 **skip**，不是 pass）|
| 3 | **akshare 返回列名** | 4 个函数的中文列名来自 akshare 1.18.64 接口定义，未实机抓取核对 | 联网后跑一次真实抓取，比对 `transform.py` 顶部的 `COL_*` 常量 |
| 4 | **巨潮接口形态** | `hisAnnouncement/query` 的表单参数、`topSearch/query` 的返回结构、必需请求头，均未实机验证；巨潮可能已加验证码/风控 | 联网后跑一次真实抓取 |
| 5 | **中证成分文件的真实格式** | 官方 `000300cons.xls` 当前**到底是** Excel、TSV 还是 HTML —— 解析器三种都支持，但**没有一种被真实文件验证过** | 联网下载一次，确认走的是哪条分支 |
| 6 | **`.xls` 解析依赖** | 若官方文件是真 Excel（BIFF），`pandas.read_excel` 需要 **`xlrd`** —— 它**不在 `pyproject.toml` 依赖里**。当前会抛 `ProviderDataError` 并提示 | 联网确认格式后：要么加 `xlrd` 依赖（需改 `pyproject.toml`，不属数据层文件所有权），要么改用 CSV 快照 |
| 7 | **契约测试部分未执行** | 因 §6.0 的依赖冲突，`respx` / `pydantic` / `sqlalchemy` 均不可用。**已实跑通过**：`test_akshare_transform.py`(27) + `test_akshare_allowlist.py`(7) = **34 条纯函数用例**，另以脚本验证了 csi300 与 cn_disclosure 的 transform。**未跑过**：`test_gateway_contract.py`(53)、`test_normalization.py`(43)、`test_csi300.py`(22)、`test_cn_disclosure.py`(19)、`test_provider_registration.py`(4) —— 这些是**写好但未执行**，不是通过 | 修好 §6.0 后跑 `python -m pytest services -q` |
| 8 | **数据源健康跨进程可见性** | `SourceHealthRegistry` 是 **worker 进程内**状态；API 进程**读不到**。spec §6 的 12 张表里没有「数据源健康表」 | 需要数据源状态页时：加一张表，或让 API 读 `jobs` 表推断。**当前没有用假数据糊过去** |
| 9 | **公告 `body_text` 为 NULL** | Agent 引用公告时只能从**标题**取 quote（spec §7.3 要求 quote 是原文连续片段）| 引入 PDF 解析（后续版本），或产品接受「公告只引标题」|
| 10 | **上线前的历史成分** | 归档目录为空 → 早于上线日的训练样本取不到成分快照，**直接报错** | 人工从中证官网补齐各调整日的官方成分文件到 `$CSI300_SNAPSHOT_DIR` |

---

## 7. 采集调度与降级（spec §8）

| 作业（`services/worker/jobs/market_data_jobs.py`）| 交易日调度 | 失败处理 |
|---|---|---|
| `sync_csi300_universe` | 07:30、18:30 | 保留上一快照并标记同步失败，**不覆盖历史有效期** |
| `ingest_watchlist_quotes` | 09:25–11:30、13:00–15:00 每 15 秒 | 指数退避；180 秒后标记 stale |
| `ingest_minute_bars` | 09:35–11:30、13:05–15:05 每 60 秒 | 按主键幂等补写 |
| `ingest_daily_bars` | 15:10、18:00 各一次 | 对账后覆盖**同源**未确认记录（`ON CONFLICT ... WHERE source = excluded.source`）|
| `ingest_announcements` | 交易时段每 5 分钟，其他时段每小时 | 按内容哈希去重 |
| `ingest_news` | 交易时段每 10 分钟，其他时段每 2 小时 | 按 URL 和内容哈希去重 |
| `run_instrument_backfill` | 自选股首次添加时 | 三步：`daily_bars` → `minute_bars` → `documents`；**分钟数据不可得只记 warning，不使整项失败** |

**降级状态**：任一数据源**连续失败 3 次** → `SourceHealth.degraded = True`。
`get_source_health()` 返回每个源的 `degraded` / `consecutive_failures` / `last_success_at` /
`last_error`，供数据源状态页展示「具体失败源 + 最后成功时间」。

**绝不静默使用缓存冒充新数据**（spec §8）：降级时作业照常失败并记账，UI 显示 stale/unavailable。
