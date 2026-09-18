param(
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"
$ScriptPath = Join-Path $RepoRoot "tools\collect_eddy_diagnostic.sh"
$Content = Get-Content -LiteralPath $ScriptPath -Encoding UTF8 -Raw

function Assert-Contains {
  param([string]$Pattern, [string]$Message)

  if ($Content -notmatch $Pattern) {
    throw "FAIL: $Message"
  }
}

function Assert-NotContains {
  param([string]$Pattern, [string]$Message)

  if ($Content -cmatch $Pattern) {
    throw "FAIL: $Message"
  }
}

Assert-Contains 'TREED_EDDY_RUN_ID' "run id is required"
Assert-Contains 'RUN_ID.*\^\[A-Za-z0-9_\]' "run id is constrained before it becomes a path or profile name"
Assert-Contains 'RUN_DIR="\$\{OUT_ROOT\}/eddy-\$\{RUN_ID\}"' "each run writes to its own package directory"
Assert-Contains 'git -C "\$\{REPO_DIR\}" cat-file -e "\$\{COMMIT\}\^\{commit\}"' "reference commit is verified before motion"
Assert-Contains 'macro_match=1' "runtime macro match is recorded"
Assert-Contains 'runtime_macro_differs_from_reference' "macro mismatch stops the scan before motion"
Assert-Contains 'ip -details -statistics link show' "can0 statistics are captured before and after the scan"
Assert-Contains 'systemctl show treed-can-setup\.service' "CAN systemd dependencies are captured"
Assert-Contains 'systemctl show klipper\.service' "Klipper systemd dependencies are captured"
Assert-Contains 'mcu-stats\.delta\.txt' "per-MCU Klipper counter deltas are written"
Assert-Contains 'candump -L "\$\{CAN_IFACE\}"' "CAN capture is passive"
Assert-Contains 'journalctl -k --since' "kernel messages use the scan interval"
Assert-Contains 'TREED_BED_MESH_CALIBRATE_EDDY PROFILE=\$\{PROFILE\} METHOD=scan' "single direct standard-area scan is submitted"
Assert-Contains 'M400' "completion marker is queued after the scan"
Assert-Contains 'result_timeout_no_repeat_sent' "timeout stops the series without another scan"
Assert-Contains 'TREED_EDDY_ALLOW_MOTION:-0' "motion requires explicit runtime confirmation"
Assert-NotContains 'SAVE_CONFIG' "diagnostic runner never saves Klipper config"
Assert-NotContains 'systemctl restart' "diagnostic runner never restarts services"
Assert-NotContains 'flash' "diagnostic runner never flashes firmware"

Write-Output "PASS: eddy diagnostic contracts"
