#!/usr/bin/env bash
# 数据源回滚（spec §19.2：禁用故障 Provider 并保留旧数据的过期标记，不删除历史记录）。
#
# 用法：
#   scripts/disable_provider.sh akshare            # 禁用
#   scripts/disable_provider.sh --enable akshare   # 恢复
#   scripts/disable_provider.sh --list             # 查看当前状态
#
# 行为：
#   * 只往 .env 写 DISABLED_PROVIDERS，然后重启 worker。
#   * 被禁用的 Provider 对应的采集作业不再排期；健康快照里状态变 disabled，
#     并保留 last_success_at —— 界面据此显示"数据可能已过期 + 最后成功时间"。
#   * 不删任何历史数据；行情/公告/新闻的过期标记由读侧按 observed_at 判定（spec §3.2）。

set -Eeuo pipefail
# shellcheck source=scripts/_common.sh
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

require_env_file

VALID_PROVIDERS=("akshare" "cn_disclosure" "csi300" "model" "internal")

current_list() { get_env_var DISABLED_PROVIDERS; }

if [[ "${1:-}" == "--list" ]]; then
  echo "DISABLED_PROVIDERS=$(current_list)"
  exit 0
fi

ENABLE=0
if [[ "${1:-}" == "--enable" ]]; then
  ENABLE=1
  shift
fi

PROVIDER="${1:-}"
[[ -n "${PROVIDER}" ]] || die "用法：scripts/disable_provider.sh [--enable|--list] <provider>"

printf '%s\n' "${VALID_PROVIDERS[@]}" | grep -qx "${PROVIDER}" \
  || die "未知 provider：${PROVIDER}（可选：${VALID_PROVIDERS[*]}）"

# 逗号分隔集合的增删
IFS=',' read -r -a items <<< "$(current_list)"
declare -a next=()
for item in "${items[@]:-}"; do
  item="$(echo "${item}" | tr -d '[:space:]')"
  [[ -z "${item}" || "${item}" == "${PROVIDER}" ]] && continue
  next+=("${item}")
done
if [[ "${ENABLE}" -eq 0 ]]; then
  next+=("${PROVIDER}")
fi

JOINED="$(IFS=','; echo "${next[*]:-}")"
set_env_var "DISABLED_PROVIDERS" "${JOINED}"

if [[ "${ENABLE}" -eq 1 ]]; then
  log "恢复数据源 ${PROVIDER}"
else
  log "禁用数据源 ${PROVIDER}（历史数据保留，不删除任何记录）"
fi

log "重启 worker 使其生效…"
compose up -d --wait --wait-timeout 180 worker

log "当前 DISABLED_PROVIDERS=${JOINED:-（空）}"
log "健康快照： docker compose exec worker cat /state/worker_health.json"
