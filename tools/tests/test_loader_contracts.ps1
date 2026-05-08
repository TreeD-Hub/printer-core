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
Assert-NotContains "loader/steps/mainsail-web.sh" 'wget -q' "mainsail-web download is not silent"
Assert-Contains "loader/steps/mainsail-web.sh" 'TREED_MAINSAIL_WGET_TIMEOUT' "mainsail-web download has bounded timeout"
Assert-Contains "loader/steps/mainsail-web.sh" 'mainsail-web: downloading Mainsail archive' "mainsail-web logs download start"
Assert-Contains "loader/steps/mainsail-web.sh" 'mainsail-web: prepared Mainsail archive' "mainsail-web logs prepared archive size"
Assert-Contains "loader/steps/mainsail-web.sh" 'if wget' "mainsail-web handles wget failure explicitly"
Assert-Contains "loader/steps/mainsail-web.sh" 'TREED_MAINSAIL_LOCAL_ZIP' "mainsail-web supports bundled local zip"
Assert-Contains "loader/steps/mainsail-web.sh" 'TREED_MAINSAIL_PREFER_LOCAL_ZIP' "mainsail-web prefers bundled local zip by default"
Assert-Contains "loader/steps/mainsail-web.sh" 'mainsail-web: using bundled Mainsail archive' "mainsail-web logs bundled archive use"
Assert-Contains "loader/steps/mainsail-web.sh" 'TREED_MAINSAIL_ALLOW_EXISTING_FALLBACK' "mainsail-web can fall back to an existing valid web root"
Assert-Contains "loader/steps/mainsail-web.sh" 'mainsail-web: using existing valid web root' "mainsail-web logs existing web root fallback"
Assert-NotContains "loader/loader.sh" 'OPTIONAL_STEPS=\([\s\S]*"klipperscreen-install"' "KlipperScreen install is required, not best-effort"
Assert-NotContains "loader/loader.sh" 'OPTIONAL_STEPS=\([\s\S]*"klipperscreen-theme"' "KlipperScreen theme is required with managed install"
Assert-NotContains "loader/loader.sh" 'OPTIONAL_STEPS=\([\s\S]*"klipperscreen-integr"' "KlipperScreen integr is required with managed install"
Assert-Contains "loader/steps/klipperscreen-install.sh" 'klipperscreen_needs_install\(\)' "KlipperScreen install has explicit version/integrity decision"
Assert-NotContains "loader/steps/klipperscreen-install.sh" 'KlipperScreen\.service already exists, skipping install' "KlipperScreen install does not bypass version/integrity checks when service exists"
Assert-Contains "loader/steps/klipperscreen-install.sh" 'KS_HOME="\$\{KS_HOME_DEFAULT\}"' "KlipperScreen install uses normal package path by default"
Assert-Contains "loader/steps/klipperscreen-install.sh" 'KS_STAGING_PARENT="\$\(dirname "\$\{KS_STAGING_DIR\}"\)"' "KlipperScreen install prepares staging parent"
Assert-Contains "loader/steps/klipperscreen-install.sh" 'chown "\$\{PI_USER\}:\$\{PI_GROUP\}" "\$\{KS_STAGING_PARENT\}"' "KlipperScreen install gives staging parent to deploy user"
Assert-Contains "loader/steps/klipperscreen-install.sh" 's\|sudo \|\|g' "KlipperScreen installer strips internal sudo for root-managed install"
Assert-Contains "loader/steps/klipperscreen-install.sh" 'USER="\$\{PI_USER\}"' "KlipperScreen installer runs with deploy user identity"
Assert-Contains "loader/steps/klipperscreen-install.sh" 'HOME="\$\{PI_HOME\}"' "KlipperScreen installer runs with deploy user home"
Assert-Contains "loader/steps/klipperscreen-install.sh" 'KLIPPERSCREEN_VENV="\$\{KS_ENV\}"' "KlipperScreen installer pins venv path under deploy home"
Assert-Contains "loader/steps/klipperscreen-install.sh" 'chown -R "\$\{PI_USER\}:\$\{PI_GROUP\}" "\$\{KS_HOME\}"' "KlipperScreen install returns package ownership to deploy user"
Assert-Contains "loader/steps/klipperscreen-install.sh" 'chown -R "\$\{PI_USER\}:\$\{PI_GROUP\}" "\$\{KS_ENV\}"' "KlipperScreen install returns venv ownership to deploy user"
Assert-Contains "loader/steps/klipperscreen-install.sh" '\[ ! -x "\$\{KS_ENV\}/bin/python" \]' "KlipperScreen install detects missing runtime venv"
Assert-Contains "loader/steps/klipperscreen-install.sh" 'KLIPPERSCREEN_INSTALL_REASON="runtime-incomplete"' "KlipperScreen install repairs partial runtime installs"
Assert-Contains "loader/steps/klipperscreen-install.sh" 'merge-base --is-ancestor "\$\{target_commit\}" "\$\{installed_head\}"' "KlipperScreen install treats installed descendant as same-or-newer"
Assert-Contains "loader/steps/klipperscreen-install.sh" 'journalctl -u "\$\{unit\}" -n' "KlipperScreen install prints journal diagnostics on service failure"
Assert-Contains "loader/steps/klipperscreen-theme.sh" 'log_error "klipperscreen-theme: styles dir not found' "KlipperScreen theme fails fast when managed install is incomplete"
Assert-Contains "loader/steps/klipperscreen-integr.sh" 'journalctl -u "\$\{unit\}" -n' "KlipperScreen integr prints journal diagnostics on service failure"
Assert-Contains "loader/steps/klipper-anti-shutdown.sh" 'wait_klippy_state' "Klipper anti-shutdown waits for terminal state before deciding"
Assert-Contains "loader/steps/klipper-anti-shutdown.sh" 'state_message.*shutdown' "Klipper anti-shutdown classifies shutdown from state_message"
Assert-Contains "loader/steps/check-env.sh" 'TREED_EBB_CANBUS_UUID is required' "check-env fails fast when EBB UUID is missing"
Assert-NotContains "loader/steps/check-env.sh" 'will try auto-detect via canbus_query' "check-env does not allow EBB UUID auto-detect"
Assert-NotContains "loader/steps/klipper-profiles.sh" 'resolve_ebb_canbus_uuid_auto' "klipper-profiles does not auto-detect EBB UUID"
Assert-NotContains "loader/steps/klipper-profiles.sh" 'canbus_query' "klipper-profiles does not use canbus_query for EBB UUID"
Assert-Contains "loader/bootstrap.sh" 'TREED_MAIN_MCU_CANBUS_UUID:=d372e54bf965' "bootstrap defaults to the detected Octopus CAN UUID"
Assert-Contains "loader/bootstrap.sh" 'TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED:=0' "bootstrap makes CAN UUID preflight diagnostic by default"
Assert-Contains "loader/bootstrap.sh" 'TREED_EBB_CANBUS_UUID:=efaf957ab20f' "bootstrap defaults to the detected EBB UUID"
Assert-Contains "loader/bootstrap.sh" 'TREED_EDDY_ENABLED:=1' "bootstrap enables Eddy by default for V2"
Assert-Contains "loader/bootstrap.sh" 'TREED_EDDY_CANBUS_UUID:=95485b93332a' "bootstrap defaults to the detected Eddy UUID"
Assert-Contains "loader/steps/check-env.sh" 'TREED_EBB_CANBUS_UUID:-efaf957ab20f' "check-env defaults to the detected EBB UUID"
Assert-Contains "loader/steps/check-env.sh" 'TREED_EDDY_ENABLED:-1' "check-env treats Eddy as enabled by default"
Assert-Contains "loader/steps/check-env.sh" 'TREED_EDDY_CANBUS_UUID:-95485b93332a' "check-env defaults to the detected Eddy UUID"
Assert-Contains "loader/steps/check-env.sh" 'TREED_MAIN_MCU_CANBUS_UUID:-d372e54bf965' "check-env defaults to the detected Octopus CAN UUID"
Assert-Contains "loader/steps/check-env.sh" 'TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED:-0' "check-env defaults CAN UUID preflight to non-blocking"
Assert-Contains "loader/steps/klipper-profiles.sh" 'TREED_MAIN_MCU_CANBUS_UUID:-d372e54bf965' "klipper-profiles defaults to the detected Octopus CAN UUID"
Assert-Contains "loader/steps/runtime-bootstrap.sh" 'CAN UUID readiness is diagnostic' "runtime preflight documents non-blocking CAN UUID readiness"
Assert-Contains "loader/steps/runtime-bootstrap.sh" 'TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED' "runtime preflight exposes strict CAN UUID gate"
Assert-Contains "loader/steps/runtime-bootstrap.sh" 'CAN UUID readiness check unavailable' "runtime preflight does not block when diagnostic query tooling is unavailable"
Assert-Contains "loader/steps/runtime-bootstrap.sh" 'CAN interface is missing .*non-blocking' "runtime preflight does not block on CAN interface in diagnostic mode"
Assert-Contains "loader/steps/maintenance-start.sh" 'journalctl -u "\$\{unit\}" -n 120' "maintenance-start prints recent journal on required service start failure"
Assert-Contains "loader/loader.sh" 'TREED_STATE_FILE="/run/treed-loader/state\.env"' "loader writes state snapshot to /run/treed-loader/state.env"
Assert-Contains "loader/loader.sh" 'detect_device_state\(\)' "loader has device state detector"
Assert-Contains "loader/loader.sh" 'TREED_DEVICE_STATE="fresh"' "loader can classify a fresh device"
Assert-Contains "loader/loader.sh" 'TREED_DEVICE_STATE="recover"' "loader can classify a recovery device"
Assert-Contains "loader/loader.sh" 'fresh\)[\s\S]*effective_mode="clean"' "auto deploy maps fresh state to clean"
Assert-Contains "loader/loader.sh" 'update\|recover\)[\s\S]*effective_mode="preserve"' "auto deploy maps update/recover state to preserve"
Assert-Contains "loader/steps/runtime-bootstrap.sh" 'CAN UUID strict gate skipped' "runtime preflight skips CAN UUID query in normal mode"
Assert-Contains "loader/steps/runtime-bootstrap.sh" 'TREED_CAMERA_REQUIRED' "runtime-bootstrap uses camera required flag for Crowsnest strictness"
Assert-Contains "loader/steps/maintenance-start.sh" 'TREED_KLIPPER_START_REQUIRE_ACTIVE' "maintenance-start can keep Klipper active wait diagnostic by default"
Assert-Contains "loader/steps/verify.sh" 'VERIFY_DIAGNOSTIC_FAILS' "verify separates diagnostic failures from fatal failures"
Assert-Contains "loader/steps/verify.sh" 'TREED_VERIFY_CAN_MCU_REQUIRED="\$\{TREED_VERIFY_CAN_MCU_REQUIRED:-1\}"' "verify requires CAN MCU connectivity by default"
Assert-Contains "loader/steps/verify.sh" 'klipper_can_mcus_connected_check "klipper CAN MCU connectivity"' "verify checks that all expected CAN MCUs connected during startup"
Assert-Contains "bootstrap-pi.sh" 'TREED_DEPLOY_MODE=auto TREED_NONINTERACTIVE=1 bash install\.sh' "Pi bootstrap runs installer in auto mode"
Assert-Contains "bootstrap-pi.sh" 'TREED_DEVICE_STATE=fresh' "Pi bootstrap reboots only after fresh install"
Assert-Contains "loader/steps/klipper-profiles.sh" 'TREED_EBB_CANBUS_UUID:-efaf957ab20f' "klipper-profiles defaults to the detected EBB UUID"
Assert-Contains "loader/steps/klipper-profiles.sh" 'TREED_EDDY_ENABLED:-1' "klipper-profiles enables Eddy by default"
Assert-Contains "loader/steps/klipper-profiles.sh" 'TREED_EDDY_CANBUS_UUID:-95485b93332a' "klipper-profiles defaults to the detected Eddy UUID"
Assert-Contains "loader/steps/firmware-build.sh" 'TREED_EDDY_ENABLED="\$\{TREED_EDDY_ENABLED:-1\}"' "firmware-build includes Eddy by default"
Assert-Contains "loader/steps/verify.sh" 'TREED_EDDY_ENABLED="\$\{TREED_EDDY_ENABLED:-1\}"' "verify expects Eddy by default"
Assert-Contains "klipper/printer.cfg" '(?m)^\[include profiles/treed_v2_corexy_v1/probe_eddy_duo_optional\.cfg\]' "repo printer.cfg enables Eddy include"
Assert-Contains "klipper/profiles/treed_v2_corexy_v1/probe_eddy_duo_optional.cfg" '(?s)\[probe_eddy_current btt_eddy\][^\[]*descend_z:\s*2\.5' "repo Eddy probe uses current descend_z parameter"
Assert-NotContains "klipper/profiles/treed_v2_corexy_v1/probe_eddy_duo_optional.cfg" '(?m)^\s*z_offset\s*:' "repo Eddy probe does not use deprecated z_offset"
Assert-NotContains "klipper/profiles/treed_v2_corexy_v1/probe_eddy_duo_optional.cfg" 'last_z_result' "repo Eddy macros do not use deprecated last_z_result"
Assert-Contains "klipper/profiles/treed_v2_corexy_v1/steppers.cfg" '(?s)\[tmc5160 stepper_x\][^\[]*cs_pin:\s*PC4' "repo stepper_x uses TMC5160 SPI on MOTOR0"
Assert-Contains "klipper/profiles/treed_v2_corexy_v1/steppers.cfg" '(?s)\[tmc5160 stepper_x\][^\[]*diag1_pin:\s*\^!PG6' "repo stepper_x uses TMC5160 DIAG1 for sensorless"
Assert-Contains "klipper/profiles/treed_v2_corexy_v1/steppers.cfg" '(?s)\[tmc5160 stepper_y\][^\[]*cs_pin:\s*PD11' "repo stepper_y uses TMC5160 SPI on MOTOR1"
Assert-Contains "klipper/profiles/treed_v2_corexy_v1/steppers.cfg" '(?s)\[tmc5160 stepper_y\][^\[]*diag1_pin:\s*\^!PG9' "repo stepper_y uses TMC5160 DIAG1 for sensorless"
Assert-NotContains "klipper/profiles/treed_v2_corexy_v1/steppers.cfg" '(?m)^\[tmc2209 stepper_x\]' "repo stepper_x does not use TMC2209"
Assert-NotContains "klipper/profiles/treed_v2_corexy_v1/steppers.cfg" '(?m)^\[tmc2209 stepper_y\]' "repo stepper_y does not use TMC2209"
Assert-Contains "klipper/profiles/treed_v2_corexy_v1/steppers.cfg" '(?s)\[stepper_x\][^\[]*endstop_pin:\s*tmc5160_stepper_x:virtual_endstop' "repo stepper_x uses sensorless virtual endstop"
Assert-Contains "klipper/profiles/treed_v2_corexy_v1/steppers.cfg" '(?s)\[stepper_y\][^\[]*endstop_pin:\s*tmc5160_stepper_y:virtual_endstop' "repo stepper_y uses sensorless virtual endstop"
Assert-Contains "klipper/profiles/treed_v2_corexy_v1/steppers.cfg" '(?s)\[stepper_z\][^\[]*endstop_pin:\s*tmc5160_stepper_z:virtual_endstop' "repo stepper_z uses TMC5160 virtual endstop for Z-max parking"
Assert-Contains "klipper/profiles/treed_v2_corexy_v1/steppers.cfg" '(?s)\[stepper_z\][^\[]*position_endstop:\s*200' "repo stepper_z parks at Z position_max"
Assert-Contains "klipper/profiles/treed_v2_corexy_v1/steppers.cfg" '(?s)\[stepper_z\][^\[]*homing_positive_dir:\s*true' "repo stepper_z homes toward Z maximum for parking"
Assert-Contains "klipper/profiles/treed_v2_corexy_v1/steppers.cfg" '(?s)\[stepper_z\][^\[]*step_pin:\s*PF11[^\[]*dir_pin:\s*!PG3[^\[]*enable_pin:\s*!PG5' "repo stepper_z is mapped to Octopus Pro MOTOR2_1 with inverted direction"
Assert-Contains "klipper/profiles/treed_v2_corexy_v1/steppers.cfg" '(?s)\[tmc5160 stepper_z\][^\[]*cs_pin:\s*PC6' "repo stepper_z uses TMC5160 SPI on MOTOR2_1"
Assert-Contains "klipper/profiles/treed_v2_corexy_v1/steppers.cfg" '(?s)\[tmc5160 stepper_z\][^\[]*diag1_pin:\s*\^!PG10' "repo stepper_z uses TMC5160 DIAG1 for sensorless Z-max parking"
Assert-Contains "klipper/profiles/treed_v2_corexy_v1/probe_eddy_duo_optional.cfg" '(?m)^\[gcode_macro TREED_Z_PARK_ZERO_EDDY\]' "repo exposes explicit Eddy Z0 parking macro"
Assert-Contains "klipper/profiles/treed_v2_corexy_v1/probe_eddy_duo_optional.cfg" '(?m)^\[gcode_macro TREED_Z_PARK_MAX_SENSORLESS\]' "repo exposes explicit sensorless Z-max parking macro"
Assert-Contains "klipper/profiles/treed_v2_corexy_v1/probe_eddy_duo_optional.cfg" '(?s)\[gcode_macro TREED_Z_PARK_MAX_SENSORLESS\][^\[]*G28 Z' "repo Z-max parking macro uses stock G28 Z path"
Assert-NotContains "klipper/profiles/treed_v2_corexy_v1/probe_eddy_duo_optional.cfg" '(?m)^\[gcode_macro G28\]' "repo does not override stock G28 command"
Assert-Contains "klipper/profiles/treed_v2_corexy_v1/macros_core.cfg" '(?s)\[gcode_macro _TREED_HOME_ALL\].*TREED_Z_PARK_ZERO_EDDY' "repo print homing path uses Eddy Z0 macro"
Assert-Contains "loader/steps/plymouth-initramfs-config.sh" 'OK \(extlinux backend, no config\.txt rewrite\)' "Plymouth initramfs config logs extlinux backend accurately"
Assert-Contains "loader/steps/crowsnest-webcam.sh" 'if \[ -f "\$\{MOONRAKER_ASVC\}" \]; then\s+chown "\$\{PI_USER\}:\$\{grp\}" "\$\{MOONRAKER_ASVC\}"' "Crowsnest webcam only chowns moonraker.asvc when it exists"

Write-Output "PASS: loader contracts"
