#!/usr/bin/env bash
# 数据库回滚（spec §19.2）。
#
#   scripts/rollback_db.sh                       # alembic downgrade -1（仅限可逆迁移）
#   scripts/rollback_db.sh --restore <dump 文件>  # pg_restore --clean --if-exists
#
# 硬约束：
#   * 标记为 IRREVERSIBLE 的迁移禁止自动回滚，必须先在数据库副本验证后在维护窗口执行。
#   * 回滚前一律再做一次 pg_dump（回滚本身也可能出错）。
#   * 不删除历史预测：downgrade 只回退 Schema，账本数据由迁移脚本自己负责。

set -Eeuo pipefail
# shellcheck source=scripts/_common.sh
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

require_env_file
mkdir -p "${BACKUP_DIR}"

MODE="downgrade"
DUMP_FILE=""
if [[ "${1:-}" == "--restore" ]]; then
  MODE="restore"
  DUMP_FILE="${2:-}"
  [[ -n "${DUMP_FILE}" && -f "${DUMP_FILE}" ]] || die "用法：scripts/rollback_db.sh --restore backups/xxx.dump"
fi

compose up -d db
compose exec -T db sh -c 'until pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; do sleep 1; done'

# 回滚前先备份当前状态
TS="$(date +%Y%m%dT%H%M%S)"
SAFETY="${BACKUP_DIR}/pre-rollback-${TS}.dump"
log "回滚前备份 → ${SAFETY}"
compose exec -T db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "${SAFETY}"
[[ -s "${SAFETY}" ]] || die "备份为空，中止回滚"

if [[ "${MODE}" == "restore" ]]; then
  log "停止写入方（api / worker），避免恢复过程中有并发写"
  compose stop api worker || true

  log "pg_restore --clean --if-exists ← ${DUMP_FILE}"
  compose exec -T db sh -c 'pg_restore --clean --if-exists -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "${DUMP_FILE}"

  log "恢复完成，重启 api / worker"
  compose up -d --wait --wait-timeout 300 api worker
  exit 0
fi

# ── alembic downgrade -1 ───────────────────────────────────────────────────────────────
if grep -rlqi 'IRREVERSIBLE' "${ROOT}/db/migrations/versions" 2>/dev/null; then
  die "存在标记为 IRREVERSIBLE 的迁移：禁止自动 downgrade（spec §19.2），请在数据库副本验证后走维护窗口"
fi

log "停止写入方（api / worker）"
compose stop api worker || true

log "alembic downgrade -1"
if ! compose run --rm -T migrate alembic downgrade -1; then
  die "downgrade 失败。可用 scripts/rollback_db.sh --restore ${SAFETY} 恢复到回滚前状态"
fi

log "重启 api / worker"
compose up -d --wait --wait-timeout 300 api worker
log "数据库回滚完成（安全备份保留在 ${SAFETY}）"
