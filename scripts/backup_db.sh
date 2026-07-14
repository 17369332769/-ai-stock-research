#!/bin/sh
# 数据库备份（spec §14.2：每日生成数据库备份，保留 7 天；spec §19.2：迁移前必须先 pg_dump）。
#
# 两种运行方式：
#   1) 宿主机： scripts/backup_db.sh [前缀]              —— 经 127.0.0.1:5432 连 db 容器
#   2) backup 容器： scripts/backup_loop.sh 每日调用     —— 经容器网络连 db:5432
#
# 用 -Fc（custom 格式），恢复命令为 pg_restore --clean --if-exists（spec §19.2）。
# POSIX sh：backup 容器是 alpine/busybox，不能用 bash 语法。

set -eu

PREFIX="${1:-daily}"

# 容器内由 compose 注入 PG*；宿主机则从 .env 读取。
if [ -z "${PGHOST:-}" ]; then
  ROOT="$(cd "$(dirname "$0")/.." && pwd)"
  if [ -f "${ROOT}/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "${ROOT}/.env"
    set +a
  fi
  PGHOST="127.0.0.1"
  PGPORT="${POSTGRES_PORT:-5432}"
  PGUSER="${POSTGRES_USER:-app}"
  PGPASSWORD="${POSTGRES_PASSWORD:-}"
  PGDATABASE="${POSTGRES_DB:-app}"
  export PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE
  BACKUP_DIR="${BACKUP_DIR:-${ROOT}/backups}"
fi

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"

mkdir -p "${BACKUP_DIR}"
TIMESTAMP="$(date +%Y%m%dT%H%M%S)"
TARGET="${BACKUP_DIR}/${PREFIX}-${TIMESTAMP}.dump"

echo "[backup] pg_dump ${PGDATABASE}@${PGHOST}:${PGPORT} -> ${TARGET}" >&2
# 先写 .partial，成功后改名：半截文件永远不会被当成可用备份
pg_dump -Fc -f "${TARGET}.partial"
mv "${TARGET}.partial" "${TARGET}"
echo "[backup] 完成 $(du -h "${TARGET}" | cut -f1)" >&2

# 保留 7 天（spec §14.2）。只清理本脚本产生的 *.dump。
find "${BACKUP_DIR}" -maxdepth 1 -name '*.dump' -type f -mtime "+${RETENTION_DAYS}" -exec rm -f {} + 2>/dev/null || true

printf '%s\n' "${TARGET}"
