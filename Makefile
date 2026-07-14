# A股 AI 研究助手 —— 开发与发布入口
#
# `make ci` 里的 8 条命令与 spec §16.1 的"CI固定命令"逐字一致，顺序也一致。

SHELL := /bin/bash
.DEFAULT_GOAL := help

PY      ?= python
COMPOSE ?= docker compose --env-file .env -f infra/docker-compose.yml

.PHONY: help
help: ## 显示所有目标
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ── 环境 ────────────────────────────────────────────────────────────────────────────────
.PHONY: init
init: ## 全新机器初始化：生成 .env、创建挂载目录
	@test -f .env || (cp .env.example .env && echo "已生成 .env（请修改 POSTGRES_PASSWORD）")
	@mkdir -p artifacts/models backups
	@echo "就绪。接着执行： make up"

.PHONY: install
install: ## 安装 Python + Node 依赖（本机开发用；容器内由镜像负责）
	$(PY) -m pip install -e ".[dev]"
	npm install

# ── CI（spec §16.1 固定命令，必须原样可跑）──────────────────────────────────────────────
.PHONY: lint
lint: ## ruff
	$(PY) -m ruff check .

.PHONY: typecheck
typecheck: ## mypy（strict）
	$(PY) -m mypy apps services

.PHONY: test
test: ## pytest
	$(PY) -m pytest -q

.PHONY: web-lint
web-lint:
	npm run lint --workspace apps/web

.PHONY: web-typecheck
web-typecheck:
	npm run typecheck --workspace apps/web

.PHONY: web-test
web-test:
	npm run test --workspace apps/web

.PHONY: web-e2e
web-e2e:
	npm run test:e2e --workspace apps/web

.PHONY: compose-config
compose-config: ## 校验 docker-compose（不需要 Docker daemon）
	docker compose -f infra/docker-compose.yml config

.PHONY: ci
ci: lint typecheck test web-lint web-typecheck web-test web-e2e compose-config ## spec §16.1 全部 8 条命令

# ── 运行 ────────────────────────────────────────────────────────────────────────────────
.PHONY: up
up: init ## 起全部服务（迁移先跑，失败则不启动，spec §19.1）
	$(COMPOSE) up -d --build --wait --wait-timeout 300

.PHONY: down
down: ## 停服务（保留数据卷）
	$(COMPOSE) down

.PHONY: logs
logs: ## 跟随日志
	$(COMPOSE) logs -f --tail=100

.PHONY: ps
ps:
	$(COMPOSE) ps

.PHONY: migrate
migrate: ## 单独执行数据库迁移
	$(COMPOSE) run --rm migrate

.PHONY: worker-health
worker-health: ## 查看调度器健康快照（失败源 + 最后成功时间，spec §8）
	$(COMPOSE) exec worker cat /state/worker_health.json

# ── 发布与回滚（spec §19）────────────────────────────────────────────────────────────────
.PHONY: release
release: ## 发布：校验 GIT_SHA → 备份 → 迁移 → 起服务
	scripts/release.sh

.PHONY: backup
backup: ## 立即做一次数据库备份（保留 7 天，spec §14.2）
	scripts/backup_db.sh manual

.PHONY: rollback-app
rollback-app: ## 应用回滚： make rollback-app SHA=<previous_git_sha>
	@test -n "$(SHA)" || (echo "用法： make rollback-app SHA=<previous_git_sha>" && exit 1)
	scripts/rollback_app.sh $(SHA)

.PHONY: rollback-db
rollback-db: ## 数据库回滚： alembic downgrade -1（不可逆迁移会被拒绝）
	scripts/rollback_db.sh

.PHONY: clean
clean: ## 清掉本机缓存产物（不动数据卷、不动 backups）
	rm -rf .mypy_cache .ruff_cache .pytest_cache
	find . -name __pycache__ -type d -not -path './.venv/*' -not -path './node_modules/*' -exec rm -rf {} + 2>/dev/null || true
