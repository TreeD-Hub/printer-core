#!/bin/bash

# ==========================================
# LOADER LIB: MANAGED RUNTIME REPOSITORY
# ==========================================
# Назначение:
# - Приводит managed source checkout к точному commit из runtime manifest.
# - Сохраняет неизвестные dirty-изменения и backup поврежденного checkout.
# Контур:
# - required; destructive reset и удаление checkout не используются.

# Блок 1: Выполнение Git-команд владельцем runtime checkout.
run_as_runtime_user() {
  if [ "$(id -un)" = "${PI_USER}" ]; then
    "$@"
  else
    sudo -u "${PI_USER}" -H "$@"
  fi
}

runtime_repo_error() {
  if command -v log_error >/dev/null 2>&1; then
    log_error "$1"
  else
    printf 'ERROR: %s\n' "$1" >&2
  fi
}

runtime_repo_info() {
  if command -v log_info >/dev/null 2>&1; then
    log_info "$1"
  else
    printf 'INFO: %s\n' "$1"
  fi
}

runtime_repo_add_excludes() {
  local repo_dir="$1"
  shift
  local exclude_file="${repo_dir}/.git/info/exclude"
  local pattern=""

  [ -d "${repo_dir}/.git" ] || return 0
  run_as_runtime_user mkdir -p "$(dirname "${exclude_file}")"
  run_as_runtime_user touch "${exclude_file}"
  for pattern in "$@"; do
    if run_as_runtime_user grep -Fxq -- "${pattern}" "${exclude_file}"; then
      continue
    fi
    if [ "$(id -un)" = "${PI_USER}" ]; then
      printf '%s\n' "${pattern}" >> "${exclude_file}"
    else
      printf '%s\n' "${pattern}" | sudo -u "${PI_USER}" -H tee -a "${exclude_file}" >/dev/null
    fi
  done
}

# Блок 2: Неразрушающий backup checkout, который нельзя безопасно чинить на месте.
backup_managed_repo() {
  local repo_dir="$1"
  local component="$2"
  local backup=""

  if [ ! -e "${repo_dir}" ]; then
    return 0
  fi

  case "${repo_dir}" in
    ""|/|"${PI_HOME}"|"${PI_HOME}/printer_data"|"${PI_HOME}/printer_data/"*)
      runtime_repo_error "${component}: refusing unsafe backup path: ${repo_dir}"
      return 1
      ;;
  esac

  backup="${repo_dir}.treed-backup-$(date +%Y%m%d-%H%M%S)-$$"
  mv "${repo_dir}" "${backup}"
  runtime_repo_info "${component}: preserved unusable checkout at ${backup}"
}

# Блок 3: Exact sync с repair detached/shallow/origin/upstream и dirty fail-safe.
sync_managed_repo() {
  local repo_dir="$1"
  local repo_url="$2"
  local repo_ref="$3"
  local primary_branch="$4"
  local component="$5"
  local dirty=""
  local shallow=""
  local target_commit=""
  local actual_commit=""

  if [ -d "${repo_dir}/.git" ]; then
    if ! run_as_runtime_user git -C "${repo_dir}" rev-parse --git-dir >/dev/null 2>&1 \
      || ! run_as_runtime_user git -C "${repo_dir}" fsck --connectivity-only >/dev/null 2>&1; then
      backup_managed_repo "${repo_dir}" "${component}" || return 1
    else
      if ! dirty="$(run_as_runtime_user git -C "${repo_dir}" status --porcelain --untracked-files=all 2>/dev/null)"; then
        backup_managed_repo "${repo_dir}" "${component}" || return 1
      elif [ -n "${dirty}" ]; then
        runtime_repo_error "${component}: checkout has unknown local changes; refusing to overwrite ${repo_dir}"
        printf '%s\n' "${dirty}" >&2
        return 1
      fi
    fi
  elif [ -e "${repo_dir}" ]; then
    backup_managed_repo "${repo_dir}" "${component}" || return 1
  fi

  if [ ! -d "${repo_dir}/.git" ]; then
    mkdir -p "$(dirname "${repo_dir}")"
    run_as_runtime_user git clone --origin origin "${repo_url}" "${repo_dir}" >/dev/null
  fi

  if run_as_runtime_user git -C "${repo_dir}" remote get-url origin >/dev/null 2>&1; then
    run_as_runtime_user git -C "${repo_dir}" remote set-url origin "${repo_url}"
  else
    run_as_runtime_user git -C "${repo_dir}" remote add origin "${repo_url}"
  fi

  shallow="$(run_as_runtime_user git -C "${repo_dir}" rev-parse --is-shallow-repository 2>/dev/null || printf 'false')"
  if [ "${shallow}" = "true" ]; then
    run_as_runtime_user git -C "${repo_dir}" fetch --unshallow --tags --prune origin >/dev/null
  else
    run_as_runtime_user git -C "${repo_dir}" fetch --tags --prune origin >/dev/null
  fi
  run_as_runtime_user git -C "${repo_dir}" fetch origin \
    "refs/heads/${primary_branch}:refs/remotes/origin/${primary_branch}" >/dev/null

  target_commit="$(run_as_runtime_user git -C "${repo_dir}" rev-parse --verify "${repo_ref}^{commit}" 2>/dev/null || true)"
  if [ -z "${target_commit}" ]; then
    runtime_repo_error "${component}: manifest ref is unavailable after fetch: ${repo_ref}"
    return 1
  fi

  run_as_runtime_user git -C "${repo_dir}" checkout -B "${primary_branch}" "${target_commit}" >/dev/null
  run_as_runtime_user git -C "${repo_dir}" branch --set-upstream-to="origin/${primary_branch}" "${primary_branch}" >/dev/null

  actual_commit="$(run_as_runtime_user git -C "${repo_dir}" rev-parse HEAD)"
  if [ "${actual_commit}" != "${repo_ref}" ]; then
    runtime_repo_error "${component}: commit mismatch expected=${repo_ref} actual=${actual_commit}"
    return 1
  fi
  if ! dirty="$(run_as_runtime_user git -C "${repo_dir}" status --porcelain --untracked-files=all 2>/dev/null)"; then
    runtime_repo_error "${component}: cannot inspect checkout after sync: ${repo_dir}"
    return 1
  fi
  if [ -n "${dirty}" ]; then
    runtime_repo_error "${component}: checkout is dirty after sync: ${repo_dir}"
    return 1
  fi

  runtime_repo_info "${component}: synced ${primary_branch} to ${actual_commit}"
}
