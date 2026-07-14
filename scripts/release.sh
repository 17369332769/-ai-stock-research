#!/usr/bin/env bash
# 本地发布（spec §19.1）。
#
# 固定顺序，任一步失败即中止，绝不启动新版本：
#   1. 校验 GIT_SHA 非空（没有提交 SHA 时发布必须失败）
#   2. 校验工作区干净（脏工作区意味着镜像内容与 SHA 对不上，标签就不再是不可变的）
#   3. 构建 app-{git_sha} 不可变标签镜像
#   4. 迁移前 pg_dump -Fc -f backups/pre-migrate-{timestamp}.dump
#   5. 检查是否存在不可逆迁移 —— 有则拒绝自动发布（spec §19.2）
#   6. 跑迁移（migrate 服务）；失败则不启动新版本
#   7. 起服务并等待健康检查
#
# 用法： scripts/release.sh            # 用当前 HEAD 的短 SHA
#        GIT_SHA=abc123 scripts/release.sh
#        ALLOW_DIRTY=1 scripts/release.sh   # 仅限本机调试，不产生可追溯发布

set -Eeuo pipefail
# shellcheck source=scripts/_common.sh
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

require_env_file

# ── 1. GIT_SHA 硬闸 ─────────────────────────────────────────────────────────────────────
SHA="$(resolve_git_sha)"
log "发布标签：app-${SHA}"

# ── 2. 工作区必须干净 ───────────────────────────────────────────────────────────────────
if [[ -n "$(git -C "${ROOT}" status --porcelain)" ]]; then
  if [[ "${ALLOW_DIRTY:-0}" == "1" ]]; then
    warn "工作区不干净，但 ALLOW_DIRTY=1：本次发布不可追溯，禁止用于正式版本"
  else
    die "工作区不干净：app-${SHA} 将无法对应到确定的代码（用 ALLOW_DIRTY=1 仅供本机调试）"
  fi
fi

export GIT_SHA="${SHA}"
set_env_var "GIT_SHA" "${SHA}"

mkdir -p "${ROOT}/artifacts/models" "${ROOT}/backups"

# ── 3. 构建不可变标签镜像 ───────────────────────────────────────────────────────────────
log "构建镜像 app-${SHA} …"
compose build

# ── 4. 迁移前备份（spec §19.2）──────────────────────────────────────────────────────────
if compose ps --status running --services 2>/dev/null | grep -qx db; then
  log "db 正在运行：迁移前先备份"
else
  log "启动 db 以便备份与迁移"
  compose up -d db
  compose exec -T db sh -c 'until pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; do sleep 1; done'
fi

if compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "select 1 from pg_tables where tablename='"'"'alembic_version'"'"'"' | grep -q 1; then
  TS="$(date +%Y%m%dT%H%M%S)"
  DUMP="${BACKUP_DIR}/pre-migrate-${TS}.dump"
  log "备份到 ${DUMP}"
  mkdir -p "${BACKUP_DIR}"
  compose exec -T db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "${DUMP}"
  [[ -s "${DUMP}" ]] || die "备份文件为空，中止发布"
else
  log "数据库还没有 alembic_version 表（首次安装），跳过迁移前备份"
fi

# ── 5. 不可逆迁移禁止自动发布（spec §19.2）───────────────────────────────────────────────
# 约定：不可逆迁移必须在 downgrade() 里显式写 IRREVERSIBLE 标记（例如 raise / 注释 # IRREVERSIBLE）。
if grep -rlqi 'IRREVERSIBLE' "${ROOT}/db/migrations/versions" 2>/dev/null; then
  PENDING="$(compose run --rm --no-deps -T migrate alembic heads 2>/dev/null | tail -n1 || true)"
  die "检测到标记为 IRREVERSIBLE 的迁移（heads=${PENDING}）：禁止自动发布，必须先在数据库副本验证并走维护窗口（spec §19.2）"
fi

# ── 6. 迁移（失败则不启动新版本）────────────────────────────────────────────────────────
log "执行数据库迁移 alembic upgrade head …"
if ! compose run --rm -T migrate; then
  die "迁移失败：不启动新版本。回滚参考 scripts/rollback_db.sh"
fi

# ── 7. 启动服务 ─────────────────────────────────────────────────────────────────────────
log "启动服务（app-${SHA}）…"
compose up -d --wait --wait-timeout 300 db openbb api worker web backup

log "发布完成：app-${SHA}"
compose ps
cat >&2 <<EOF

访问：
  Web     http://127.0.0.1:3000
  API     http://127.0.0.1:8000
  OpenBB  http://127.0.0.1:6900

回滚：
  应用    scripts/rollback_app.sh <上一个 git_sha>
  数据库  scripts/rollback_db.sh [--restore backups/xxx.dump]
  模型    scripts/rollback_model.sh <model_key> <上一个 version>
  数据源  scripts/disable_provider.sh <akshare|cn_disclosure|csi300>
EOF
