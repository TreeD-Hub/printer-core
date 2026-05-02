# TreeD MainshellOS: V2 модель владения конфигами и слоями

Документ фиксирует фактическую модель для ветки `treed-v2`.
Если поведение в runtime и текст документа расходятся, источником истины считается код шагов loader.

## 1. Точки входа и порядок шагов

Entrypoint:
- `loader/loader.sh`

Порядок шагов:
1. `check-env`
2. `detect-rpi`
3. `timezone-sync`
4. `maintenance-stop`
5. `packages-core`
6. `can-setup`
7. `firmware-build`
8. `boot-hdmi-config`
9. `plymouth-theme-install`
10. `plymouth-initramfs`
11. `plymouth-initramfs-config`
12. `plymouth-cmdline`
13. `plymouth-systemd`
14. `klipper-sync`
15. `klipper-profiles`
16. `klipper-core`
17. `klipper-adxl-rpi`
18. `klipper-anti-shutdown`
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

## 4. Контракт V2: main/CAN/Eddy

Required:
- `TREED_EBB_CANBUS_UUID`

Optional:
- `TREED_MAIN_MCU_SERIAL_BY_ID`
- `TREED_MAIN_MCU_SERIAL_MASK` (default `/dev/serial/by-id/*stm32*`)
- `TREED_CAN_IFACE` (default `can0`)
- `TREED_CAN_BITRATE` (default `500000`)
- `TREED_CAN_TXQUEUE` (default `1024`)
- `TREED_EDDY_ENABLED` (`0|1`, default `0`)
- `TREED_EDDY_CANBUS_UUID` (обязателен только при `TREED_EDDY_ENABLED=1`)

Fail-fast сценарии:
- auto-resolve main MCU: `0` кандидатов -> fail;
- auto-resolve main MCU: `>1` кандидатов -> fail;
- `TREED_EDDY_ENABLED=1` и пустой `TREED_EDDY_CANBUS_UUID` -> fail.

## 5. Что не используется в `treed-v2`

- RN12-профиль как active runtime-контур.
- UART-transport main MCU и связанные проверки.
- RPi/UART migration сценарии в основном install path.
