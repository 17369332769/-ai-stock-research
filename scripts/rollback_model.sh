#!/usr/bin/env bash
# 模型回滚（spec §19.2：将上一模型版本重新标记为 active，保留新模型产生的预测审计记录）。
#
# 用法： scripts/rollback_model.sh <model_key> <target_version>
#   例： scripts/rollback_model.sh today_close 2026-07-10-a
#
# 硬约束：
#   * 只改 model_versions.status（active ↔ retired），一条事务内原子切换。
#   * 绝不删除 predictions / prediction_outcomes：由被回滚模型产生的预测保留其原始
#     model_version_id，继续按原模型结算，不重算、不删除（spec §19.2 末段、验收 §15.8）。
#   * 目标版本必须已经存在且不是 candidate —— candidate 永远不对 API 提供预测（spec §9.4）。

set -Eeuo pipefail
# shellcheck source=scripts/_common.sh
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

require_env_file

MODEL_KEY="${1:-}"
TARGET_VERSION="${2:-}"
[[ -n "${MODEL_KEY}" && -n "${TARGET_VERSION}" ]] \
  || die "用法：scripts/rollback_model.sh <model_key> <target_version>"

psql_q() {
  compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$1"' _ "$1"
}

log "当前 ${MODEL_KEY} 的版本状态："
compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"' _ \
  "SELECT version, status, created_at FROM model_versions WHERE model_key = '${MODEL_KEY}' ORDER BY created_at DESC LIMIT 10;"

EXISTS="$(psql_q "SELECT count(*) FROM model_versions WHERE model_key='${MODEL_KEY}' AND version='${TARGET_VERSION}' AND status IN ('active','retired');")"
[[ "${EXISTS}" == "1" ]] \
  || die "目标版本不存在或仍是 candidate：${MODEL_KEY}/${TARGET_VERSION}（candidate 永远不得对外提供预测，spec §9.4）"

log "原子切换：把 ${MODEL_KEY}/${TARGET_VERSION} 标记为 active，其余 active 版本转 retired"
compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"' _ "
BEGIN;
  UPDATE model_versions
     SET status = 'retired'
   WHERE model_key = '${MODEL_KEY}' AND status = 'active' AND version <> '${TARGET_VERSION}';
  UPDATE model_versions
     SET status = 'active'
   WHERE model_key = '${MODEL_KEY}' AND version = '${TARGET_VERSION}';
COMMIT;
"

KEPT="$(psql_q "SELECT count(*) FROM predictions p JOIN model_versions m ON m.id = p.model_version_id WHERE m.model_key='${MODEL_KEY}';")"
log "回滚完成。${MODEL_KEY} 的历史预测 ${KEPT} 条全部保留（按原 model_version_id 继续结算）"

compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"' _ \
  "SELECT version, status FROM model_versions WHERE model_key = '${MODEL_KEY}' ORDER BY created_at DESC LIMIT 10;"

log "提示：模型产物目录 artifacts/models/${MODEL_KEY}/${TARGET_VERSION}/ 必须仍然存在（容器内 /models 只读挂载）"
