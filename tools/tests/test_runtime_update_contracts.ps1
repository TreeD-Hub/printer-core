$ErrorActionPreference = "Stop"

# ==========================================
# CONTRACT TEST: REPRODUCIBLE RUNTIME UPDATE
# ==========================================
# Назначение:
# - Фиксирует manifest/order/version/hardware contracts loader.
# - Запускает поведенческий test managed Git recovery через Git Bash.
# Контур:
# - runnable локально без доступа к принтеру и внешним сервисам.

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$bash = "C:\Program Files\Git\bin\bash.exe"
if (-not (Test-Path -LiteralPath $bash)) {
  throw "Git Bash is required: $bash"
}

function Read-RepoFile([string]$Path) {
  return Get-Content -LiteralPath (Join-Path $repoRoot $Path) -Raw -Encoding UTF8
}

function Assert-Match([string]$Path, [string]$Pattern, [string]$Message) {
  if ((Read-RepoFile $Path) -notmatch $Pattern) {
    throw "FAIL: $Message ($Path)"
  }
}

$bashTest = (Join-Path $repoRoot "tools\tests\test_runtime_repo_sync.sh").Replace("\", "/").Replace("C:", "/c")
& $bash $bashTest
if ($LASTEXITCODE -ne 0) {
  throw "runtime repository sync test failed"
}

$manifest = Read-RepoFile "runtime-versions.env"
foreach ($name in @(
  "TREED_KLIPPER_REF", "TREED_MOONRAKER_REF", "TREED_KLIPPERSCREEN_REF", "TREED_CROWSNEST_REF"
)) {
  if ($manifest -notmatch "(?m)^$name=`"[0-9a-f]{40}`"$") {
    throw "FAIL: $name must be a full immutable SHA"
  }
}
Assert-Match "runtime-versions.env" '(?m)^TREED_MAINSAIL_VERSION="v2\.19\.0"$' "Mainsail is version-pinned"
Assert-Match "runtime-versions.env" '(?m)^TREED_MAINSAIL_ZIP_SHA256="[0-9a-f]{64}"$' "Mainsail artifact has SHA-256"

$loader = Read-RepoFile "loader\loader.sh"
if ($loader.IndexOf('"runtime-bootstrap"') -ge $loader.IndexOf('"firmware-build"')) {
  throw "FAIL: runtime-bootstrap must precede firmware-build"
}

Assert-Match "loader\steps\runtime-bootstrap.sh" 'ensure_repo_present "\$\{KLIPPER_DIR\}".*"Klipper"' "Klipper uses exact managed sync"
Assert-Match "loader\steps\runtime-bootstrap.sh" 'ensure_repo_present "\$\{MOONRAKER_DIR\}".*"Moonraker"' "Moonraker uses exact managed sync"
Assert-Match "loader\steps\firmware-build.sh" 'KLIPPER_HEAD.*TREED_KLIPPER_REF' "firmware build rejects unsynced Klipper"
Assert-Match "loader\steps\firmware-build.sh" 'artifact_sha256.*klipper_commit.*config_sha256' "firmware manifest records source and checksums"
Assert-Match "loader\steps\verify.sh" 'sha256sum.*firmware_artifact' "verify checks firmware artifact content"
Assert-Match "loader\steps\moonraker-config.sh" 'MOONRAKER_COMPONENTS_DIR=.*moonraker/components' "components deploy to active managed checkout"
Assert-Match "loader\steps\runtime-bootstrap.sh" "runtime_repo_add_excludes" "managed Moonraker components do not leave checkout dirty"
Assert-Match "loader\steps\moonraker-config.sh" 'restart_and_verify_moonraker_components' "Moonraker restarts and verifies components"
Assert-Match "loader\steps\mainsail-web.sh" 'archive checksum mismatch' "Mainsail artifact checksum is enforced"
Assert-Match "loader\steps\klipperscreen-install.sh" 'installed package matches manifest' "KlipperScreen requires exact manifest commit"
Assert-Match "loader\steps\verify.sh" 'TREED_ALLOW_HARDWARE_NOT_READY:-0' "production hardware gate is default"
Assert-Match "loader\steps\verify.sh" "mcu 'EBBCan': Unable to connect" "EBB connection errors are fatal by default"
Assert-Match "loader\steps\verify.sh" 'hardware_failf "Klipper ready required' "Klipper ready is a production hardware gate"
Assert-Match "loader\steps\verify.sh" 'for component in treed_shell_command treed_host_network treed_filament_sensor treed_update' "TreeD component load is verified"
Assert-Match "loader\steps\verify.sh" 'Mainsail version expected=' "Mainsail installed version is verified"

Write-Output "PASS: reproducible runtime update contracts"
