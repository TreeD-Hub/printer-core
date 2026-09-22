#!/bin/bash
set -euo pipefail

# ==========================================
# CONTRACT TEST: RUNTIME REPOSITORY SYNC
# ==========================================
# Назначение:
# - Проверяет exact sync старого/detached/shallow/wrong-origin checkout.
# - Проверяет dirty fail-safe, backup поврежденного checkout и идемпотентность.
# Контур:
# - runnable локально; использует только временные Git-репозитории.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PI_USER="$(id -un)"
PI_HOME="${HOME}"
export PI_USER PI_HOME

log_info() { printf 'INFO: %s\n' "$*"; }
log_error() { printf 'ERROR: %s\n' "$*" >&2; }

. "${REPO_DIR}/loader/lib/runtime-repo.sh"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
remote="${tmp}/remote.git"
seed="${tmp}/seed"

git init --bare --initial-branch=master "${remote}" >/dev/null
git init --initial-branch=master "${seed}" >/dev/null
git -C "${seed}" config user.name test
git -C "${seed}" config user.email test@example.invalid
printf 'old\n' > "${seed}/runtime.txt"
git -C "${seed}" add runtime.txt
git -C "${seed}" commit -m old >/dev/null
old_commit="$(git -C "${seed}" rev-parse HEAD)"
git -C "${seed}" remote add origin "${remote}"
git -C "${seed}" push -u origin master >/dev/null

git clone "${remote}" "${tmp}/old" >/dev/null
printf 'target\n' > "${seed}/runtime.txt"
git -C "${seed}" commit -am target >/dev/null
target_commit="$(git -C "${seed}" rev-parse HEAD)"
git -C "${seed}" push origin master >/dev/null

assert_synced() {
  local path="$1"
  [ "$(git -C "${path}" rev-parse HEAD)" = "${target_commit}" ]
  [ "$(git -C "${path}" symbolic-ref --short HEAD)" = "master" ]
  [ "$(git -C "${path}" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}')" = "origin/master" ]
  [ "$(git -C "${path}" rev-parse --is-shallow-repository)" = "false" ]
  [ -z "$(git -C "${path}" status --porcelain --untracked-files=all)" ]
}

# Блок 1: Старый checkout обновляется и повторный sync ничего не меняет.
sync_managed_repo "${tmp}/old" "${remote}" "${target_commit}" master Klipper
assert_synced "${tmp}/old"
sync_managed_repo "${tmp}/old" "${remote}" "${target_commit}" master Klipper
assert_synced "${tmp}/old"

# Блок 2: Detached HEAD нормализуется в manifest branch.
git clone "${remote}" "${tmp}/detached" >/dev/null
git -C "${tmp}/detached" checkout --detach "${old_commit}" >/dev/null
sync_managed_repo "${tmp}/detached" "${remote}" "${target_commit}" master Moonraker
assert_synced "${tmp}/detached"

# Блок 3: Shallow checkout разворачивается до полной истории.
git clone --depth 1 "file://${remote}" "${tmp}/shallow" >/dev/null
sync_managed_repo "${tmp}/shallow" "${remote}" "${target_commit}" master Crowsnest
assert_synced "${tmp}/shallow"

# Блок 4: Неверный origin чинится до fetch.
git clone "${remote}" "${tmp}/wrong-origin" >/dev/null
git -C "${tmp}/wrong-origin" remote set-url origin "${tmp}/missing.git"
sync_managed_repo "${tmp}/wrong-origin" "${remote}" "${target_commit}" master KlipperScreen
assert_synced "${tmp}/wrong-origin"
[ "$(git -C "${tmp}/wrong-origin" ls-remote origin refs/heads/master | awk '{print $1}')" = "${target_commit}" ]

# Блок 5: Dirty checkout блокируется без потери файла.
git clone "${remote}" "${tmp}/dirty" >/dev/null
printf 'local-change\n' >> "${tmp}/dirty/runtime.txt"
if sync_managed_repo "${tmp}/dirty" "${remote}" "${target_commit}" master Klipper; then
  printf 'dirty checkout unexpectedly synced\n' >&2
  exit 1
fi
grep -Fx 'local-change' "${tmp}/dirty/runtime.txt" >/dev/null

# Блок 6: Известные managed-файлы не делают checkout dirty.
git clone "${remote}" "${tmp}/managed" >/dev/null
runtime_repo_add_excludes "${tmp}/managed" '/managed-runtime.txt'
printf 'managed\n' > "${tmp}/managed/managed-runtime.txt"
sync_managed_repo "${tmp}/managed" "${remote}" "${target_commit}" master Moonraker
assert_synced "${tmp}/managed"
[ -f "${tmp}/managed/managed-runtime.txt" ]

# Блок 7: Повреждённая Git metadata сохраняется перед восстановлением.
git clone "${remote}" "${tmp}/corrupt" >/dev/null
printf 'broken-index\n' > "${tmp}/corrupt/.git/index"
sync_managed_repo "${tmp}/corrupt" "${remote}" "${target_commit}" master Moonraker
assert_synced "${tmp}/corrupt"
corrupt_backup="$(find "${tmp}" -maxdepth 1 -type d -name 'corrupt.treed-backup-*' -print -quit)"
[ -n "${corrupt_backup}" ]

# Блок 8: Некорректный каталог сохраняется рядом и заменяется валидным checkout.
mkdir "${tmp}/broken"
printf 'keep-me\n' > "${tmp}/broken/local.txt"
sync_managed_repo "${tmp}/broken" "${remote}" "${target_commit}" master Moonraker
assert_synced "${tmp}/broken"
backup="$(find "${tmp}" -maxdepth 1 -type d -name 'broken.treed-backup-*' -print -quit)"
[ -n "${backup}" ]
grep -Fx 'keep-me' "${backup}/local.txt" >/dev/null

printf 'PASS: runtime repository sync\n'
