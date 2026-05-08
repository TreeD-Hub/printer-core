> Root project map: `README.md`
> Canonical config ownership model: `docs/config-ownership.md`

## Быстрый install path (V2)

```bash
curl -fsSL https://raw.githubusercontent.com/TreeD-Hub/treed-mainshellOS/treed-v2/bootstrap-pi.sh | bash
```

## Контракт переменных

- `TREED_MAIN_MCU_CANBUS_UUID` — Octopus Pro UUID, default `d372e54bf965`.
- `TREED_CAN_IFACE` — default `can0`.
- `TREED_CAN_BITRATE` — default `1000000`.
- `TREED_CAN_TXQUEUE` — default `1024`.
- `TREED_DEPLOY_MODE` — `auto|clean|preserve`, default `auto`; auto выбирает режим по `/run/treed-loader/state.env`.
- `TREED_KLIPPER_PREFLIGHT` — `0|1`, default `1`; включает readiness-проверку перед стартом Klipper.
- `TREED_KLIPPER_PREFLIGHT_WAIT_SEC` — default `12`; общий таймаут ожидания CAN-интерфейса.
- `TREED_KLIPPER_PREFLIGHT_INTERVAL_SEC` — default `1`; интервал повторной проверки.
- `TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED` — `0|1`, default `0`; при `1` strict UUID-gate через `canbus_query.py`, при `0` query не запускается.
- `TREED_EBB_CANBUS_UUID` — EBB42 UUID, default `efaf957ab20f`; auto-detect не используется, чтобы не принять Eddy за EBB.
- `TREED_EDDY_ENABLED` — `0|1`, default `1`.
- `TREED_EDDY_CANBUS_UUID` — Eddy UUID, default `95485b93332a`.
- `TREED_NONINTERACTIVE` — `0|1`, default `1`; disables apt/dpkg/needrestart prompts.
- `TREED_KLIPPERSCREEN_INSTALL_SERVICE` — answer for KlipperScreen service install, default `1`.
- `TREED_KLIPPERSCREEN_BACKEND` — answer for KlipperScreen graphical backend, default `X`.
- `TREED_KLIPPERSCREEN_NETWORK_MANAGER` — answer for NetworkManager install, default `N`.
- `TREED_KLIPPERSCREEN_START_AFTER_INSTALL` — answer for external installer service start, default `0`.

## Контракт железа

- Host SBC: Rock Pi / Rock Pi 4 Plus.
- Main MCU: Octopus Pro по CAN.
- CAN adapter: U2C V2.1 (USB -> CAN).
- Toolhead MCU: EBB42 по CAN (required).
- Probe: Eddy / Eddy Duo по CAN (enabled by default).
- Z: при Eddy enabled рабочий Z0 ищется через Eddy, сервисная нижняя парковка стола идет через TMC5160 sensorless к Zmax.

Ветка `treed-v2` не поддерживает RN12/RPi/UART-миграции.
