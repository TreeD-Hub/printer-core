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

function Assert-NotContains {
  param(
    [string]$Path,
    [string]$Pattern,
    [string]$Message
  )

  $content = Get-Content -Encoding UTF8 -LiteralPath (Join-Path $RepoRoot $Path) -Raw
  if ($content -match $Pattern) {
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
Assert-NotContains "loader/loader.sh" 'OPTIONAL_STEPS=\([\s\S]*"klipperscreen-install"' "KlipperScreen install is required, not best-effort"
Assert-NotContains "loader/loader.sh" 'OPTIONAL_STEPS=\([\s\S]*"klipperscreen-theme"' "KlipperScreen theme is required with managed install"
Assert-NotContains "loader/loader.sh" 'OPTIONAL_STEPS=\([\s\S]*"klipperscreen-integr"' "KlipperScreen integr is required with managed install"
Assert-Contains "loader/steps/klipperscreen-install.sh" 'klipperscreen_needs_install\(\)' "KlipperScreen install has explicit version/integrity decision"
Assert-NotContains "loader/steps/klipperscreen-install.sh" 'KlipperScreen\.service already exists, skipping install' "KlipperScreen install does not bypass version/integrity checks when service exists"
Assert-Contains "loader/steps/klipperscreen-install.sh" 'KS_HOME="\$\{KS_HOME_DEFAULT\}"' "KlipperScreen install uses normal package path by default"
Assert-Contains "loader/steps/klipperscreen-install.sh" 'merge-base --is-ancestor "\$\{target_commit\}" "\$\{installed_head\}"' "KlipperScreen install treats installed descendant as same-or-newer"
Assert-Contains "loader/steps/klipperscreen-install.sh" 'journalctl -u "\$\{unit\}" -n' "KlipperScreen install prints journal diagnostics on service failure"
Assert-Contains "loader/steps/klipperscreen-theme.sh" 'log_error "klipperscreen-theme: styles dir not found' "KlipperScreen theme fails fast when managed install is incomplete"
Assert-Contains "loader/steps/klipperscreen-integr.sh" 'journalctl -u "\$\{unit\}" -n' "KlipperScreen integr prints journal diagnostics on service failure"
Assert-Contains "loader/steps/klipper-anti-shutdown.sh" 'wait_klippy_state' "Klipper anti-shutdown waits for terminal state before deciding"
Assert-Contains "loader/steps/klipper-anti-shutdown.sh" 'state_message.*shutdown' "Klipper anti-shutdown classifies shutdown from state_message"
Assert-Contains "loader/steps/plymouth-initramfs-config.sh" 'OK \(extlinux backend, no config\.txt rewrite\)' "Plymouth initramfs config logs extlinux backend accurately"
Assert-Contains "loader/steps/crowsnest-webcam.sh" 'if \[ -f "\$\{MOONRAKER_ASVC\}" \]; then\s+chown "\$\{PI_USER\}:\$\{grp\}" "\$\{MOONRAKER_ASVC\}"' "Crowsnest webcam only chowns moonraker.asvc when it exists"

Write-Output "PASS: loader contracts"
