param(
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"

function Assert-Contains {
  param(
    [string]$Path,
    [string]$Pattern,
    [string]$Message
  )

  $content = Get-Content -Encoding UTF8 -LiteralPath (Join-Path $RepoRoot $Path) -Raw
  if ($content -notmatch $Pattern) {
    throw "FAIL: $Message ($Path)"
  }
}

Assert-Contains "loader/steps/runtime-bootstrap.sh" 'TREED_CROWSNEST_REPO' "runtime-bootstrap exposes Crowsnest repo configuration"
Assert-Contains "loader/steps/runtime-bootstrap.sh" 'ensure_crowsnest_runtime' "runtime-bootstrap installs or updates Crowsnest runtime"
Assert-Contains "loader/steps/runtime-bootstrap.sh" 'CROWSNEST_UNATTENDED=1' "Crowsnest install runs unattended"
Assert-Contains "loader/steps/runtime-bootstrap.sh" 'systemctl daemon-reload\s+if ! systemctl cat crowsnest\.service' "runtime-bootstrap reloads systemd before validating installed crowsnest service"
Assert-Contains "loader/steps/runtime-bootstrap.sh" 'if \[ -d "\$\{CROWSNEST_DIR\}" \]; then\s+chown -R "\$\{PI_USER\}:\$\{PI_GROUP\}" "\$\{CROWSNEST_DIR\}"' "runtime-bootstrap does not chown a missing Crowsnest checkout when install is disabled"
Assert-Contains "loader/bootstrap.sh" 'export TREED_CROWSNEST_INSTALL' "bootstrap forwards Crowsnest install contract into loader steps"
Assert-Contains "loader/steps/crowsnest-webcam.sh" 'skip_webcam_deploy "crowsnest\.service is missing"' "optional camera path cleans stale webcam config when crowsnest service is missing"
Assert-Contains "loader/steps/verify.sh" 'auto: crowsnest\.service missing' "verify auto mode skips camera HTTP checks when crowsnest service is missing"
Assert-Contains "loader/README.md" 'TREED_CROWSNEST_REPO' "loader docs include top-level Crowsnest bootstrap variables"
Assert-Contains "loader/steps/README.md" 'TREED_CROWSNEST_REPO' "loader step docs include Crowsnest bootstrap variables"

Write-Output "PASS: loader contracts"
