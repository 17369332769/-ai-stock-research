#!/bin/sh
# backup 容器入口：每天在 BACKUP_AT（默认 02:30，Asia/Shanghai）跑一次 pg_dump，保留 7 天（spec §14.2）。
# 纯算术计算下次触发点：alpine 的 busybox date 不支持 "tomorrow" 这类相对时间。

set -eu

BACKUP_AT="${BACKUP_AT:-02:30}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"

echo "[backup-loop] 每日 ${BACKUP_AT} (${TZ:-UTC}) 备份到 ${BACKUP_DIR}，保留 ${RETENTION_DAYS} 天" >&2

target_hour="${BACKUP_AT%%:*}"
target_min="${BACKUP_AT##*:}"
target_sec=$(( 10#${target_hour} * 3600 + 10#${target_min} * 60 ))

while true; do
  now_sec=$(( 10#$(date +%H) * 3600 + 10#$(date +%M) * 60 + 10#$(date +%S) ))
  delta=$(( target_sec - now_sec ))
  [ "${delta}" -le 0 ] && delta=$(( delta + 86400 ))

  echo "[backup-loop] 下次备份在 ${delta} 秒后" >&2
  sleep "${delta}"

  # 备份失败不得让容器退出（spec §14.2：故障不崩溃、不丢历史）；等下一天重试。
  if sh /scripts/backup_db.sh daily; then
    echo "[backup-loop] 备份成功" >&2
  else
    echo "[backup-loop] 备份失败，保留既有备份，等待下一次" >&2
  fi
done
