# TreeD MainshellOS: V2 модель владения конфигами и слоями

Документ фиксирует фактическую модель для ветки `treed-v2`.
Если поведение в runtime и текст документа расходятся, источником истины считается код шагов loader.

## 1. Точки входа и порядок шагов

Entrypoint:
- `loader/loader.sh`

Порядок шагов:
1. `check-env`
2. `detect-boot-env`
3. `timezone-sync`
4. `maintenance-stop`
5. `packages-core`
6. `runtime-bootstrap`
7. `can-setup`
8. `firmware-build`
9. `boot-hdmi-config`
10. `plymouth-theme-install`
11. `plymouth-initramfs`
12. `plymouth-initramfs-config`
13. `plymouth-cmdline`
14. `plymouth-systemd`
15. `klipper-sync`
16. `klipper-core`
17. `klipper-anti-shutdown`
18. `mainsail-web`
19. `moonraker-config`
20. `crowsnest-webcam`
21. `treed-cam`
22. `klipper-mainsail-theme`
23. `klipperscreen-install`
24. `klipperscreen-theme`
25. `klipperscreen-integr`
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
- `/home/pi/treed/klipper` (результат `klipper-sync`)

Runtime:
- `/home/pi/printer_data/config` (раскладка `klipper-core`)
- `/home/pi/treed/cam/bin` (раскладка `treed-cam`)
- `${TREED_KLIPPERSCREEN_HOME}/styles/treed-oled` (раскладка `klipperscreen-theme`)

## 3. Ownership map (runtime)

Управляется репозиторием и шагами loader:
- `/home/pi/printer_data/config/printer.cfg`
- `/home/pi/printer_data/config/profiles/treed_v2_corexy_v1/*`
- `/home/pi/printer_data/config/moonraker.conf`
- `/home/pi/printer_data/config/moonraker/base/*.conf`
- `/home/pi/printer_data/config/.theme/*`

Генерируется loader-шагами:
- `/home/pi/printer_data/config/moonraker/generated/50-webcam-treed.conf`
  владелец: `loader/steps/crowsnest-webcam.sh`

Локальные runtime-overrides:
- `/home/pi/printer_data/config/local_overrides.cfg`

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
- `TREED_CAN_TXQUEUE` (default `1024`)
- `TREED_KLIPPER_PREFLIGHT` (`0|1`, default `1`)
- `TREED_KLIPPER_PREFLIGHT_WAIT_SEC` (default `12`)
- `TREED_KLIPPER_PREFLIGHT_INTERVAL_SEC` (default `1`)
- `TREED_KLIPPER_PREFLIGHT_CAN_UUIDS_REQUIRED` (`0|1`, default `0`)
- `TREED_EDDY_ENABLED` (`0|1`, default `1`)
- `TREED_EDDY_CANBUS_UUID` (default `95485b93332a`)

При `TREED_EDDY_ENABLED=1` профиль использует Eddy как штатный рабочий Z-контур:
- `stepper_z` работает через `probe:z_virtual_endstop`;
- `G28 Z`, полный `G28` и `TREED_Z_PARK_ZERO_EDDY` ищут рабочий Z0 через Eddy после сохраненной `PROBE_EDDY_CURRENT_CALIBRATE`;
- Zmax sensorless DIAG оставлен только как аппаратный резерв вне основного Eddy-профиля.

Fail-fast сценарии:
- пустой `TREED_MAIN_MCU_CANBUS_UUID` -> fail;
- `TREED_EDDY_ENABLED=1` и пустой `TREED_EDDY_CANBUS_UUID` -> fail.

## 5. Что не используется в `treed-v2`

- RN12-профиль как active runtime-контур.
- UART-transport main MCU и связанные проверки.
- RPi/UART migration сценарии в основном install path.
