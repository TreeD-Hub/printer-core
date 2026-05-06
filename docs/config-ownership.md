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
16. `klipper-profiles`
17. `klipper-core`
18. `klipper-anti-shutdown`
19. `mainsail-web`
20. `moonraker-config`
21. `crowsnest-webcam`
22. `treed-cam`
23. `klipper-mainsail-theme`
24. `klipperscreen-install`
25. `klipperscreen-theme`
26. `klipperscreen-integr`
27. `maintenance-start`
28. `verify`

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

## 4. Контракт V2: main/CAN/Eddy

Required:
- `TREED_EBB_CANBUS_UUID` (default `efaf957ab20f`; auto-detect не используется)

Optional:
- `TREED_MAIN_MCU_SERIAL_BY_ID` (default `/dev/serial/by-id/usb-Klipper_stm32f446xx_3B0027000D50535556323420-if00`)
- `TREED_MAIN_MCU_SERIAL_MASK` (default `/dev/serial/by-id/*stm32*`)
- `TREED_CAN_IFACE` (default `can0`)
- `TREED_CAN_BITRATE` (default `1000000`)
- `TREED_CAN_TXQUEUE` (default `1024`)
- `TREED_EDDY_ENABLED` (`0|1`, default `1`)
- `TREED_EDDY_CANBUS_UUID` (default `95485b93332a`)
- `TREED_Z_ENDSTOP_PIN` (default `PG10`, используется при `TREED_EDDY_ENABLED=0`)
- `TREED_Z_POSITION_ENDSTOP` (default `0.5`, используется при `TREED_EDDY_ENABLED=0`)

При `TREED_EDDY_ENABLED=1` профиль использует два раздельных Z-контура:
- `TREED_Z_PARK_ZERO_EDDY` ищет рабочий Z0 через Eddy;
- `TREED_Z_PARK_MAX_SENSORLESS` опускает стол к Zmax через `tmc5160_stepper_z:virtual_endstop`.

Fail-fast сценарии:
- auto-resolve main MCU: `0` кандидатов -> fail;
- auto-resolve main MCU: `>1` кандидатов -> fail;
- `TREED_EDDY_ENABLED=1` и пустой `TREED_EDDY_CANBUS_UUID` -> fail.

## 5. Что не используется в `treed-v2`

- RN12-профиль как active runtime-контур.
- UART-transport main MCU и связанные проверки.
- RPi/UART migration сценарии в основном install path.
