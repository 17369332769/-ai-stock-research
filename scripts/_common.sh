#!/usr/bin/env bash
# 发布/回滚脚本公共部分。所有脚本都以仓库根为工作目录，并显式指定 --env-file，
# 避免 compose 从 infra/ 目录去找 .env（否则 GIT_SHA 插值会静默变成 dev 标签）。

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT}/infra/docker-compose.yml"
ENV_FILE="${ROOT}/.env"
BACKUP_DIR="${ROOT}/backups"

# Docker 需要 sudo 时设置 DOCKER="sudo docker"
DOCKER="${DOCKER:-docker}"

log()  { printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[fail]\033[0m %s\n' "$*" >&2; exit 1; }

compose() {
  # shellcheck disable=SC2086
  ${DOCKER} compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
}

require_env_file() {
  [[ -f "${ENV_FILE}" ]] || die ".env 不存在：先 cp .env.example .env 并填好数据库口令"
}

# spec §19.1：镜像标签必须是不可变的 app-{git_sha}；没有提交 SHA 时发布必须失败。
resolve_git_sha() {
  local sha="${GIT_SHA:-}"
  if [[ -z "${sha}" ]]; then
    sha="$(git -C "${ROOT}" rev-parse --short=12 HEAD 2>/dev/null || true)"
  fi
  [[ -n "${sha}" ]] || die "GIT_SHA 为空且仓库没有提交：拒绝发布（spec §19.1）"
  [[ "${sha}" != "latest" ]] || die "拒绝使用 latest 标签发布（spec §19.1）"
  printf '%s' "${sha}"
}

# 把 KEY=VALUE 写回 .env（存在则替换，不存在则追加）。密钥不落日志。
set_env_var() {
  local key="$1" value="$2"
  require_env_file
  if grep -qE "^${key}=" "${ENV_FILE}"; then
    # 用 | 作分隔符：值里可能有 /
    sed -i -E "s|^${key}=.*|${key}=${value}|" "${ENV_FILE}"
  else
    printf '%s=%s\n' "${key}" "${value}" >> "${ENV_FILE}"
  fi
  log "已写入 .env：${key}=${value}"
}

get_env_var() {
  local key="$1"
  [[ -f "${ENV_FILE}" ]] || return 0
  sed -nE "s|^${key}=(.*)$|\1|p" "${ENV_FILE}" | tail -n 1
}
