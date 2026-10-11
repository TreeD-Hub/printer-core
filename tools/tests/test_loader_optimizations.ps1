$ErrorActionPreference = "Stop"

# ==========================================
# CONTRACT TEST: LOADER OPTIMIZATIONS
# ==========================================
# Назначение:
# - запускает реальные helper-функции с подменёнными apt/systemctl;
# - проверяет кэш APT-индекса и очистку webcam-фрагментов на временных файлах.
# Контур:
# - runnable offline: не загружает весь installer и не обращается к сервисам.

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$treedSource = Get-Content -Raw (Join-Path $repoRoot "loader\steps\treed-shell-install.sh")
$webcamSource = Get-Content -Raw (Join-Path $repoRoot "loader\steps\crowsnest-webcam.sh")
$treedStart = $treedSource.IndexOf("ensure_apt_index() {")
$treedEnd = $treedSource.IndexOf("# Блок 4: Release artifact download/extract.", $treedStart)
$webcamStart = $webcamSource.IndexOf("skip_webcam_deploy() {")
$webcamEnd = $webcamSource.IndexOf("crowsnest_service_available() {", $webcamStart)
if ($treedStart -lt 0 -or $treedEnd -lt 0 -or $webcamStart -lt 0 -or $webcamEnd -lt 0) {
  throw "Loader helper function boundaries changed"
}

$gitCommand = Get-Command git -ErrorAction Stop
$bashPath = if ($IsWindows) {
  Join-Path (Split-Path (Split-Path $gitCommand.Source -Parent) -Parent) "bin/bash.exe"
} else {
  (Get-Command bash -ErrorAction Stop).Source
}
if (-not (Test-Path -LiteralPath $bashPath)) { throw "Bash not found: $bashPath" }
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("loader-optimizations-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tempRoot | Out-Null
$scriptPath = Join-Path $tempRoot "contract.sh"
try {
  $bashScript = @'
set -euo pipefail
__TREED_FUNCTIONS__
__WEBCAM_FUNCTIONS__

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
assert_eq() { [ "$1" = "$2" ] || fail "$3 (expected '$2', got '$1')"; }

INSTALLED_PACKAGES=""
UPDATE_RESULTS=()
UPDATE_CALLS=0
INSTALL_CALLS=0
INSTALL_LOG=""
OPTIONAL_INSTALL_RC=0
DEFAULT_INSTALL_RC=0
CHROMIUM_RC=0
FALLBACK_RC=0
dpkg-query() {
  case " ${INSTALLED_PACKAGES} " in
    *" $3 "*) printf 'install ok installed'; return 0 ;;
    *) return 1 ;;
  esac
}
apt_update_noninteractive() {
  UPDATE_CALLS=$((UPDATE_CALLS + 1))
  local rc=0
  if [ "${#UPDATE_RESULTS[@]}" -gt 0 ]; then
    rc="${UPDATE_RESULTS[0]}"
    UPDATE_RESULTS=("${UPDATE_RESULTS[@]:1}")
  fi
  return "${rc}"
}
apt_get_noninteractive() {
  INSTALL_CALLS=$((INSTALL_CALLS + 1))
  local package="${@: -1}"
  INSTALL_LOG+=" ${package}"
  case "${package}" in
    unclutter) return "${OPTIONAL_INSTALL_RC}" ;;
    chromium) return "${CHROMIUM_RC}" ;;
    chromium-browser)
      if [ "${FALLBACK_RC}" -eq 0 ]; then FALLBACK_INSTALLED=1; fi
      return "${FALLBACK_RC}"
      ;;
    *) return "${DEFAULT_INSTALL_RC}" ;;
  esac
}
log_info() { :; }
log_warn() { :; }

# Одно успешное обновление обслуживает несколько обязательных групп.
APT_INDEX_UPDATED=0
UPDATE_RESULTS=(0)
install_missing_packages base-ui browser-support
assert_eq "$UPDATE_CALLS" 1 "APT update should run once for missing groups"
assert_eq "$INSTALL_CALLS" 1 "Missing packages should be installed in one group"
install_missing_packages extra-ui
assert_eq "$UPDATE_CALLS" 1 "Cached APT index should serve another missing group"
assert_eq "$INSTALL_CALLS" 2 "Second missing group should still be installed"

# Полностью установленный набор не требует обновления индекса.
APT_INDEX_UPDATED=0
INSTALLED_PACKAGES="base-ui browser-support"
UPDATE_CALLS=0
INSTALL_CALLS=0
install_missing_packages base-ui browser-support
assert_eq "$UPDATE_CALLS" 0 "APT update should be skipped when all packages exist"
assert_eq "$INSTALL_CALLS" 0 "No package install should run when all packages exist"

# Неудачный optional refresh не кэшируется; required refresh повторяется.
APT_INDEX_UPDATED=0
INSTALLED_PACKAGES=""
UPDATE_RESULTS=(1 0)
UPDATE_CALLS=0
INSTALL_CALLS=0
install_optional_unclutter
install_missing_packages browser-support
assert_eq "$UPDATE_CALLS" 2 "Required install should retry a failed optional refresh"
assert_eq "$INSTALL_CALLS" 1 "Required packages should install after retry"

# Повторная ошибка required browser refresh прекращает установку.
APT_INDEX_UPDATED=0
UPDATE_RESULTS=(1)
UPDATE_CALLS=0
INSTALL_CALLS=0
resolve_browser_bin() { return 1; }
if ensure_browser_runtime; then fail "required browser refresh failure should fail"; fi
assert_eq "$UPDATE_CALLS" 1 "Required browser refresh should be attempted"
assert_eq "$INSTALL_CALLS" 0 "Browser packages must not install after failed refresh"

# Ошибка обязательной установки сохраняет свой ненулевой код.
APT_INDEX_UPDATED=0
UPDATE_RESULTS=(0)
UPDATE_CALLS=0
INSTALL_CALLS=0
DEFAULT_INSTALL_RC=42
if install_missing_packages browser-support; then fail "required package install failure should fail"; else assert_eq "$?" 42 "Required install exit status"; fi
assert_eq "$UPDATE_CALLS" 1 "Required install should refresh the index first"

# Ошибка optional install остаётся best-effort.
APT_INDEX_UPDATED=0
UPDATE_RESULTS=(0)
UPDATE_CALLS=0
DEFAULT_INSTALL_RC=0
OPTIONAL_INSTALL_RC=23
install_optional_unclutter
assert_eq "$UPDATE_CALLS" 1 "Optional package should refresh index when needed"

# Chromium fallback сохраняется после отказа основного пакета.
APT_INDEX_UPDATED=0
UPDATE_RESULTS=(0)
UPDATE_CALLS=0
INSTALL_LOG=""
CHROMIUM_RC=1
FALLBACK_RC=0
OPTIONAL_INSTALL_RC=0
FALLBACK_INSTALLED=0
resolve_browser_bin() {
  if [ "$FALLBACK_INSTALLED" = 1 ]; then printf '%s\n' chromium-browser; return 0; fi
  return 1
}
ensure_browser_runtime
case "${INSTALL_LOG}" in *" chromium chromium-browser") : ;; *) fail "browser fallback package order" ;; esac
assert_eq "$BROWSER_BIN" chromium-browser "Fallback browser should be selected"

# Webcam cleanup fixtures use only temp paths and a stub systemctl.
fixture="${TMPDIR:-/tmp}/loader-webcam-test-$$"
mkdir -p "${fixture}"
MOONRAKER_WEBCAM_FRAGMENT="${fixture}/50-webcam.conf"
CROWSNEST_CONF="${fixture}/crowsnest.conf"
trap 'command rm -f -- "$MOONRAKER_WEBCAM_FRAGMENT" "$CROWSNEST_CONF"; rmdir "$fixture"' EXIT
MOONRAKER_SERVICE=1
CROWSNEST_SERVICE=0
STOP_RC=0
RESTART_RC=0
STOP_CALLS=0
RESTART_CALLS=0
RM_FAIL_PATH=""
systemctl() {
  case "$1:$2" in
    cat:moonraker.service) [ "$MOONRAKER_SERVICE" = 1 ] ;;
    cat:crowsnest.service) [ "$CROWSNEST_SERVICE" = 1 ] ;;
    stop:crowsnest.service) STOP_CALLS=$((STOP_CALLS + 1)); return "$STOP_RC" ;;
    restart:moonraker.service) RESTART_CALLS=$((RESTART_CALLS + 1)); return "$RESTART_RC" ;;
    *) return 2 ;;
  esac
}
rm() {
  for path in "$@"; do
    [ "$path" = "${RM_FAIL_PATH}" ] && return 9
  done
  command rm -f "$@"
}

# Без webcam-фрагмента Moonraker не перезапускается.
skip_webcam_deploy absent
assert_eq "$RESTART_CALLS" 0 "Absent fragment should not restart Moonraker"

# Фрагмент удаляется, Moonraker перезапускается один раз; повторный вызов не повторяет рестарт.
: > "$MOONRAKER_WEBCAM_FRAGMENT"
skip_webcam_deploy present
assert_eq "$RESTART_CALLS" 1 "Removed fragment should trigger one restart"
skip_webcam_deploy repeated
assert_eq "$RESTART_CALLS" 1 "Repeated cleanup should not restart again"

# Один crowsnest.conf удаляется и сервис останавливается без Moonraker restart.
CROWSNEST_SERVICE=1
: > "$CROWSNEST_CONF"
skip_webcam_deploy crowsnest-only
[ ! -e "$CROWSNEST_CONF" ] || fail "crowsnest config should be removed"
assert_eq "$STOP_CALLS" 1 "Existing crowsnest service should be stopped"
assert_eq "$RESTART_CALLS" 1 "crowsnest-only cleanup should not restart Moonraker"

# Нет Moonraker unit: фрагмент удаляется, ошибка отсутствующей службы допустима.
MOONRAKER_SERVICE=0
: > "$MOONRAKER_WEBCAM_FRAGMENT"
skip_webcam_deploy missing-moonraker
assert_eq "$RESTART_CALLS" 1 "Missing Moonraker unit should not restart"

# Ошибка удаления не скрывается и не приводит к restart.
MOONRAKER_SERVICE=1
: > "$MOONRAKER_WEBCAM_FRAGMENT"
RM_FAIL_PATH="$MOONRAKER_WEBCAM_FRAGMENT"
if skip_webcam_deploy rm-failure; then fail "rm failure should propagate"; fi
assert_eq "$RESTART_CALLS" 1 "Failed removal should not restart Moonraker"
[ -e "$MOONRAKER_WEBCAM_FRAGMENT" ] || fail "failed removal should leave the fragment in place"
RM_FAIL_PATH=""

# Ошибки stop/restart остаются best-effort.
STOP_RC=7
RESTART_RC=8
: > "$MOONRAKER_WEBCAM_FRAGMENT"
: > "$CROWSNEST_CONF"
skip_webcam_deploy tolerated-service-errors
assert_eq "$RESTART_CALLS" 2 "Restart should be attempted after successful removal"
assert_eq "$STOP_CALLS" 3 "Stop should still be attempted when it fails"
printf 'loader optimizations contract: OK\n'
'@
  $bashScript = $bashScript.Replace("__TREED_FUNCTIONS__", $treedSource.Substring($treedStart, $treedEnd - $treedStart))
  $bashScript = $bashScript.Replace("__WEBCAM_FUNCTIONS__", $webcamSource.Substring($webcamStart, $webcamEnd - $webcamStart))
  [IO.File]::WriteAllText($scriptPath, $bashScript, [Text.UTF8Encoding]::new($false))
  if ($scriptPath -match '^([A-Za-z]):\\') {
    $bashScriptPath = "/$($Matches[1].ToLowerInvariant())/" + $scriptPath.Substring(3).Replace('\', '/')
  } else {
    $bashScriptPath = $scriptPath
  }
  & $bashPath $bashScriptPath
  if ($LASTEXITCODE -ne 0) { throw "Bash contract test failed with exit code $LASTEXITCODE" }
}
finally {
  if (Test-Path -LiteralPath $scriptPath) { [IO.File]::Delete($scriptPath) }
  if (Test-Path -LiteralPath $tempRoot) { [IO.Directory]::Delete($tempRoot) }
}
