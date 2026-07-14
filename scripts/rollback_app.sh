#!/usr/bin/env bash
# 应用回滚（spec §19.2）：把 .env 里的镜像标签改回上一个 git_sha，然后 docker compose up -d。
#
# 用法： scripts/rollback_app.sh <previous_git_sha>
#
# 注意（spec §19.2 末段）：
#   * 不动数据库、不动预测账本。由被回滚版本创建的预测继续按其原始 model_version_id 结算，
#     不重算、不删除。
#   * 旧应用必须能读新 Schema；因此这里只切镜像，不做 alembic downgrade。
#     确需回滚 Schema 时用 scripts/rollback_db.sh，并先确认迁移是可逆的。

set -Eeuo pipefail
# shellcheck source=scripts/_common.sh
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

require_env_file

TARGET_SHA="${1:-}"
[[ -n "${TARGET_SHA}" ]] || die "用法：scripts/rollback_app.sh <previous_git_sha>（不接受空值和 latest）"
[[ "${TARGET_SHA}" != "latest" ]] || die "拒绝回滚到 latest：标签必须不可变（spec §19.1）"

CURRENT="$(get_env_var GIT_SHA)"
log "当前标签：app-${CURRENT:-未知} → 回滚到：app-${TARGET_SHA}"

# 镜像必须已在本机存在，否则回滚会卡在构建/拉取上
for image in api worker web openbb; do
  if ! ${DOCKER} image inspect "ai-stock-research/${image}:app-${TARGET_SHA}" >/dev/null 2>&1; then
    die "本机没有镜像 ai-stock-research/${image}:app-${TARGET_SHA}：无法回滚（请先 GIT_SHA=${TARGET_SHA} scripts/release.sh 构建，或保留旧镜像）"
  fi
done

set_env_var "GIT_SHA" "${TARGET_SHA}"
export GIT_SHA="${TARGET_SHA}"

log "切换到 app-${TARGET_SHA} …"
compose up -d --wait --wait-timeout 300 db openbb api worker web backup

log "回滚完成：app-${TARGET_SHA}（数据库与预测账本未改动）"
compose ps
