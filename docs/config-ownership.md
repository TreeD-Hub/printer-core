# TreeD Printer Core: V2 модель владения конфигами и слоями

Документ фиксирует фактическую модель для ветки `treed-v2`.
Если поведение в runtime и текст документа расходятся, источником истины считается код шагов loader.

## 1. Точки входа и порядок шагов

Entrypoint:
- `loader/loader.sh`

Порядок шагов:
1. `check-env`
2. `timezone-sync`
3. `maintenance-stop`
4. `packages-core`
5. `runtime-bootstrap`
6. `can-setup`
7. `firmware-build`
8. `boot-hdmi-config`
9. `plymouth-theme-install`
10. `plymouth-initramfs`
11. `plymouth-initramfs-config`
12. `plymouth-cmdline`
13. `plymouth-systemd`
14. `klipper-sync`
15. `klipper-core`
16. `klipper-anti-shutdown`
17. `mainsail-web`
18. `moonraker-config`
19. `crowsnest-webcam`
20. `treed-cam`
21. `klipper-mainsail-theme`
22. `klipperscreen-install`
23. `klipperscreen-theme`
24. `klipperscreen-integr`
25. `treed-shell-install`
26. `maintenance-start`
27. `verify`

## 2. Слои и source of truth

Repo (источник правды):
- `klipper/*`
- `moonraker/*`
- `runtime-scripts/*`
- `mainsail/.theme/*`
- `klipperscreen/themes/*`

Staging:
- `${PI_HOME}/treed/klipper` (результат `klipper-sync`)

Runtime:
- `${PI_HOME}/printer_data/config` (раскладка `klipper-core`)
- `${PI_HOME}/treed/cam/bin` (раскладка `treed-cam`)
- `${TREED_KLIPPERSCREEN_HOME}/styles/treed-oled` (раскладка `klipperscreen-theme`)

## 3. Ownership map (runtime)

Управляется репозиторием и шагами loader:
- `${PI_HOME}/printer_data/config/printer.cfg`
- `${PI_HOME}/printer_data/config/profiles/treed_v2_corexy_v1/*`
- `${PI_HOME}/printer_data/config/moonraker.conf`
- `${PI_HOME}/printer_data/config/moonraker/base/*.conf`
- `${PI_HOME}/printer_data/config/.theme/*`

Генерируется loader-шагами:
- `${PI_HOME}/printer_data/config/moonraker/generated/50-webcam-treed.conf`
  владелец: `loader/steps/crowsnest-webcam.sh`

Локальные runtime-overrides:
- `${PI_HOME}/printer_data/config/local_overrides.cfg`

CAN-host слой:
- `/etc/default/treed-can-setup`
- `/usr/local/sbin/treed-can-setup.sh`
- `/etc/systemd/system/treed-can-setup.service`
  владелец: `loader/steps/can-setup.sh`

Klipper cold-start слой:
- `/etc/default/treed-klipper-preflight`
- `/usr/local/sbin/treed-klipper-preflight.sh`
- `ExecStartPre=/usr/local/sbin/treed-klipper-preflight.sh` в `/etc/systemd/system/klipper.service`
  владелец: `loader/steps/runtime-bootstrap.sh`

## 4. Контракт V2: main/CAN/Eddy

Required:
- `TREED_MAIN_MCU_CANBUS_UUID` (default `d372e54bf965`; auto-detect не используется)
- `TREED_EBB_CANBUS_UUID` (default `efaf957ab20f`; auto-detect не используется)

Optional:
- `TREED_CAN_IFACE` (default `can0`)
- `TREED_CAN_BITRATE` (default `1000000`)
- `TREED_CAN_TXQUEUE` (default `128`)
- `TREED_KLIPPER_PREFLIGHT` (`0|1`, default `1`)
- `TREED_KLIPPER_PREFLIGHT_WAIT_SEC` (default `12`)
- `TREED_KLIPPER_PREFLIGHT_INTERVAL_SEC` (default `1`)
- `TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED` (`0|1`, default `0`)
- `TREED_EDDY_ENABLED` (legacy `0|1`, default `1`; Eddy required для активного Klipper-профиля, значение `0` не поддерживается)
- `TREED_EDDY_CANBUS_UUID` (default `95485b93332a`)

Активный профиль `treed_v2_corexy_v1` использует Eddy как обязательный штатный Z-контур:
- `stepper_z` работает через `probe:z_virtual_endstop`;
- `G28` переопределен профилем и маршрутизирует Z-home в `_TREED_EDDY_HOME_Z`;
- `G28 Z` и полный `G28` используют Eddy как штатный `probe:z_virtual_endstop`;
- `TREED_Z_PARK_ZERO_EDDY` остается публичной командой рабочего Z0 через Eddy;
- Zmax sensorless DIAG оставлен только как аппаратный резерв вне основного Eddy-профиля.

Fail-fast сценарии:
- пустой `TREED_MAIN_MCU_CANBUS_UUID` -> fail;
- `TREED_EDDY_ENABLED=1` и пустой `TREED_EDDY_CANBUS_UUID` -> fail; значение `0` несовместимо с активным профилем.

## 5. Что не используется в `treed-v2`

- RN12-профиль как active runtime-контур.
- UART-transport main MCU и связанные проверки.
- RPi/UART migration сценарии в основном install path.
