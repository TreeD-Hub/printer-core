# Profile `rn12_hbot_v1` (MKS Robin Nano 1.2)

This profile follows the active loader pipeline in `loader/loader.sh`.
Canonical ownership and deploy model: `docs/config-ownership.md`.

## Entry point

Current Klipper entry point:
- `klipper/printer.cfg`

`printer.cfg` includes profile modules directly (no `root.cfg` chain):
- `mcu_rn12.cfg`
- `printer_base.cfg`
- `steppers.cfg`
- `extruder.cfg`
- `bed_heater_dc.cfg`
- `fans.cfg`
- `macros.cfg`
- `ui.cfg`
- `local_overrides.cfg`

## Loader deploy flow

1. `loader/steps/klipper-sync.sh` copies `klipper/` to staging at `/home/pi/treed/klipper`.
2. `loader/steps/klipper-profiles.sh` updates `serial:` in `mcu_rn12.cfg` for fixed profile `rn12_hbot_v1`.
3. `loader/steps/klipper-core.sh` copies full staging tree to runtime `/home/pi/printer_data/config`.

## Local overrides

- Template in repo: `klipper/local_overrides.example.cfg`.
- Runtime local file: `local_overrides.cfg`.
- `local_overrides.cfg` is not committed and is preserved by `klipper-core`.

## Legacy note

Legacy `printer_root.cfg`/`root.cfg` model is not active in the current repository state.
When docs differ, trust:
1. `loader/loader.sh`
2. `docs/config-ownership.md`
3. `klipper/printer.cfg`
