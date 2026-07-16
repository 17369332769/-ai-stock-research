# A股 AI 研究助手（MVP）

把行情、公告、新闻、异动解释、概率预测、历史相似行情和**可审计的预测成绩单**放进一个本地优先的研究界面。
股票池限定为**沪深300当前成分股**。预测只是一个可审计模块：任何结论都带数据截止时间、证据来源、
模型版本和历史表现。

> **仅供研究，不构成投资建议。** 不自动下单，不连接实盘交易账户，不承诺收益率。

权威规格：[`ai-stock-research-assistant-spec.html`](./ai-stock-research-assistant-spec.html)（v1.1）。
本 README 与规格冲突时，以规格为准。

---

## 1. 快速开始（全新机器）

前置：Docker 24+（含 compose v2）、Git。不需要在宿主机装 Python 或 Node —— 全部跑在容器里。

```bash
git clone <repo> ai-stock-research && cd ai-stock-research

# 1) 生成 .env 并创建挂载目录（artifacts/models、backups）
make init
$EDITOR .env          # 至少改掉 POSTGRES_PASSWORD

# 2) 起全部服务：db → openbb → 迁移 → api / worker / web
#    迁移在服务启动前执行；迁移失败则新版本不会启动（spec §19.1）
make up

# 3) 成分同步后自动形成300只沪深300研究池，无需逐只添加
curl -s 'http://127.0.0.1:8000/api/v1/research-pool?scope=csi300'

# 4) 打开研究页（首次回补期间页面会显示回补进度，不显示未经训练的预测）
open http://127.0.0.1:3000/stocks/600519
```

服务与端口（**全部只绑 `127.0.0.1`**，spec §14.3）：

| 服务 | 地址 | 说明 |
|---|---|---|
| web | http://127.0.0.1:3000 | Next.js 研究界面 |
| api | http://127.0.0.1:8000 | FastAPI REST（`/api/v1`） |
| openbb | http://127.0.0.1:6900 | OpenBB Platform 内部 REST（**唯一**可触达外部数据源的进程） |
| db | 127.0.0.1:5432 | PostgreSQL 16 |
| worker | 无端口 | APScheduler 调度器，只出不进 |

常用命令：

```bash
make ps              # 服务状态
make logs            # 跟随日志
make worker-health   # 调度器健康快照：失败的数据源 + 最后成功时间
make audit-training-data # 训练前数据质量审计
make train-models    # 泄漏测试 + walk-forward + 登记 candidate
make backup          # 立即备份数据库
make down            # 停服务（保留数据卷）
```

---

## 2. 架构

```
AKShare行情 ──────────┐
巨潮/交易所公告 ──────┼─> OpenBB 自定义 Provider ─> OpenBB 内部 REST (:6900)
中证指数成分 ─────────┤                                    │
公开新闻源 ───────────┘                                    v
                                            数据采集器(worker) ─> PostgreSQL
                                                                  │
                                                                  ├─> 特征与异动检测
                                                                  ├─> Qlib/LightGBM 预测
                                                                  ├─> 历史相似状态
                                                                  └─> 证据约束 Agent
                                                                            │
   Next.js (:3000) <─────────────────────────  FastAPI (:8000) ─────────────┘
```

铁律（spec §4.2）：

* 业务代码**只**调用内部 OpenBB REST；只有 `services/openbb_extensions/` 里的 Provider 可以访问
  AKShare / 巨潮 / 交易所 / 中证指数。
* OpenBB 不保存业务状态：外部数据必须先规范化落库，才能进入模型和 Agent。
* 行情记录必须带 `source` / `source_url` / `observed_at`；文档另存 `published_at`。
* 训练与回测必须 point-in-time：禁止使用预测时点之后发布的数据。
* 定量概率来自量化模型/校准器，**不来自大语言模型的文字判断**。

### 进程分工

| 进程 | 职责 | 不做 |
|---|---|---|
| `api` | 权限边界、输入校验、DTO、业务编排 | 采集解析、训练算法 |
| `worker` | 调度作业、重试、作业锁、运行记录 | 业务专用逻辑（在 `services/worker/jobs/`） |
| `openbb` | 三个自定义 Provider | 保存业务状态 |
| `web` | 展示与交互 | 业务计算 |

后台采集在独立的 `worker` 进程里跑，**不阻塞 API**（spec §14.1）。

---

## 3. 调度（spec §8）

全部按 `Asia/Shanghai`，交易日历以交易所日历（XSHG）为准；**非交易日一律跳过**
（周末与法定休市日都靠 `get_trading_calendar()` 判定，不是靠"星期几"）。

| 作业 | 交易日调度 | 失败处理 |
|---|---|---|
| 沪深300成分同步 | 07:30、18:30 | 保留上一快照并标记同步失败，不覆盖历史有效期 |
| 研究池报价 | 09:25–11:30、13:00–15:00 每15秒处理一批 | 免费单股源约2–5分钟完成300只；详情页优先刷新 |
| 5分钟K线 | 09:35–11:30、13:05–15:05 每 60 秒 | 按主键幂等补写 |
| 日线 | 15:10、18:00 | 对账后覆盖同源未确认记录 |
| 公告 | 交易时段每 5 分钟；其他时段每小时 | 按内容哈希去重 |
| 新闻 | 交易时段每 10 分钟；其他时段每 2 小时 | 按 URL + 内容哈希去重 |
| 今日预测 | 09:45 起每 15 分钟，最后一次 14:45 | 模型不可用则显示 unavailable |
| 一周预测 | 09:45、11:30、15:20 | 保留全部版本，不覆盖 |
| 预测结算 | 15:20，及次日 08:30 补偿 | 幂等；交易日顺延 |

规格表未给出时刻、但功能章节要求"每日/持续"的作业（时刻为工程推断，见
`services/worker/scheduler.py` 注释）：

| 作业 | 时刻 | 依据 |
|---|---|---|
| 特征漂移 PSI | 18:30 | §9.3.1「每日计算特征PSI」，排在日线 18:00 之后 |
| 异动检测 | 交易时段每 5 分钟（09:40–15:05） | §12，依赖 5 分钟K线 |
| 分析刷新 | 交易时段每 30 分钟 + 15:30 | §11 |
| 回补分发 | 每 10 秒（**非交易日也跑**） | §3.1 / §14.1：API 只入队，worker 执行 |

失败与降级：作业失败**永不**让进程崩溃；同一作业不并发（作业锁）；重试用指数退避；
**连续失败 3 次进入降级状态**，健康快照里给出失败的数据源与**最后成功时间**
（`make worker-health` 或容器内 `/state/worker_health.json`，API 只读挂载后供
`/settings/data-sources` 展示）。绝不静默拿缓存冒充新数据。

---

## 4. CI（spec §16.1 固定命令）

```bash
python -m ruff check .
python -m mypy apps services
python -m pytest -q
npm run lint --workspace apps/web
npm run typecheck --workspace apps/web
npm run test --workspace apps/web
npm run test:e2e --workspace apps/web
docker compose -f infra/docker-compose.yml config
```

一次跑完：`make ci`。GitHub Actions 配置在 `.github/workflows/ci.yml`
（额外加了一条"端口必须只绑 127.0.0.1"的静态检查）。

测试禁止访问公网：Provider 响应用 `tests/fixtures/providers/` 里的脱敏夹具；时间用可注入
`Clock` 固定（覆盖 09:44 / 09:45 / 11:30 / 13:00 / 15:00 / 节假日）；交易日历用测试夹具。

---

## 5. 发布与回滚（spec §19）

### 发布

```bash
scripts/release.sh          # 或 GIT_SHA=<sha> scripts/release.sh
```

顺序（任一步失败即中止，绝不启动新版本）：

1. 校验 `GIT_SHA` 非空 —— **没有提交 SHA 时发布脚本直接失败**；工作区必须干净。
2. 构建不可变标签镜像 `app-{git_sha}`（**不允许只用 `latest`**）。
3. 迁移前 `pg_dump -Fc -f backups/pre-migrate-{timestamp}.dump`。
4. 检出被标记为 `IRREVERSIBLE` 的迁移则拒绝自动发布（只能在数据库副本验证后走维护窗口）。
5. `alembic upgrade head`；失败则不启动新版本。
6. `docker compose up -d --wait`。

模型产物不进镜像：`artifacts/models/{model_key}/{version}/` 以**只读**方式挂载到容器 `/models`
（`artifact_uri = file:///models/{model_key}/{version}`），版本由 `model_versions` 表管理，
compose 标签记录 `model-${MODEL_VERSION}`。

### 回滚

| 场景 | 命令 | 保证 |
|---|---|---|
| 应用 | `scripts/rollback_app.sh <上一个 git_sha>` | 改 `.env` 镜像标签 + `compose up -d`；不动数据库与预测账本 |
| 数据库 | `scripts/rollback_db.sh`<br>`scripts/rollback_db.sh --restore backups/x.dump` | `alembic downgrade -1`；**不可逆迁移禁止自动回滚**；回滚前再备份一次 |
| 模型 | `scripts/rollback_model.sh <model_key> <version>` | 上一版本重标 `active`，**保留新模型产生的全部预测审计记录** |
| 数据源 | `scripts/disable_provider.sh <provider>` | 禁用故障 Provider，保留旧数据的过期标记，**不删历史** |

由回滚版本创建的预测继续按其原始 `model_version_id` 结算，不重算、不删除。
预测账本核心字段由数据库触发器保护，任何 UPDATE 都会被拒绝。

### 备份

`backup` 容器每天 02:30（Asia/Shanghai）执行 `pg_dump -Fc`，**保留 7 天**（spec §14.2）。
手动备份：`make backup`。恢复：`pg_restore --clean --if-exists -d app backups/{file}.dump`。

---

## 6. 安全（spec §14.3）

* Docker 端口默认只绑 `127.0.0.1`。容器内部监听 `0.0.0.0` 只在容器网络内可见。
* API 密钥只从环境变量 / `.env` 注入，**不写进镜像、不写进数据库、不写进日志**；
  worker 的错误信息在落盘前做密钥脱敏。
* Agent 只能读产品数据库，不读本机任意文件；外部文档一律视为不可信内容，
  不允许改变系统提示或调用权限。
* 容器以非 root 用户运行；`/models` 只读挂载。

---

## 7. 许可证与数据来源

* 本项目代码：**AGPL-3.0-only**（见 `pyproject.toml`）。
* **OpenBB Platform 采用 AGPL-3.0。本 MVP 只允许本地个人使用，不得公开托管。**
  公开发布或商业使用前，必须单独完成许可证审查与底层数据授权审查（spec §4.2）。
* 数据来源：AKShare（行情/K线/新闻）、巨潮资讯与上交所/深交所（公告原文）、中证指数（沪深300成分）。
  **免费公开数据仅限个人研究，不得重新分发。** 每个上游来源的使用条款、访问日期、频率限制和
  延迟说明记录在 `docs/data-sources.md`；缺少该记录时发布检查失败。
* 模型卡（特征 Schema、数据范围、指标、基准对比、已知局限）：`docs/model-card.md`。

---

## 8. 目录

```
apps/api/        FastAPI：路由、DTO、仓储、编排
apps/web/        Next.js：研究页、成绩单、数据源状态页
services/
  worker/        APScheduler 调度器（scheduler.py）、运行器（runner.py）、作业（jobs/）
  market_data/   OpenBB 网关客户端、规范化、校验、去重
  openbb_extensions/  AKShare / 法定披露 / CSI300 三个自定义 Provider
  prediction/    point-in-time 特征、训练、推理、回测、相似行情、结算
  research/      证据检索与结构化解释
db/migrations/   Alembic
config/features/ 特征集版本（{feature_set_version}.yaml）
infra/           docker-compose + 各服务 Dockerfile
scripts/         发布、回滚、备份
```
