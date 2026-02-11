# TreeD MainshellOS: модель владения конфигами и слоями

Документ фиксирует фактическую модель, которая сейчас реализована в `loader/loader.sh`.
Если поведение в рантайме и текст документа расходятся, источником истины считается код шагов loader.

## 1. Точки входа и порядок шагов

Entrypoint:
- `loader/loader.sh`

Полный порядок шагов:
1. `check-env`
2. `detect-rpi`
3. `timezone-sync`
4. `maintenance-stop`
5. `packages-core`
6. `boot-hdmi-config`
7. `rpi-uart-config`
8. `plymouth-theme-install`
9. `plymouth-initramfs`
10. `plymouth-initramfs-config`
11. `plymouth-cmdline`
12. `plymouth-systemd`
13. `klipper-sync`
14. `klipper-profiles`
15. `klipper-core`
16. `klipper-anti-shutdown`
17. `moonraker-config`
18. `crowsnest-webcam`
19. `treed-cam`
20. `klipper-mainsail-theme`
21. `klipperscreen-install`
22. `klipperscreen-theme`
23. `klipperscreen-integr`
24. `maintenance-start`
25. `verify`

## 2. Слои и source of truth

Repo (источник правды):
- `klipper/*`
- `moonraker/*`
- `runtime-scripts/*`
- `mainsail/.theme/*`
- `klipperscreen/themes/*`

Staging:
- `/home/pi/treed/klipper` (результат шага `klipper-sync`)

Runtime:
- `/home/pi/printer_data/config` (раскладка шага `klipper-core`)
- `/home/pi/treed/cam/bin` (раскладка шага `treed-cam`)
- `/home/pi/KlipperScreen/styles/treed-oled` (раскладка шага `klipperscreen-theme`)

## 3. Ownership map (runtime)

Управляется репозиторием и шагами loader:
- `/home/pi/printer_data/config/printer.cfg`
- `/home/pi/printer_data/config/profiles/*`
- `/home/pi/printer_data/config/moonraker.conf`
- `/home/pi/printer_data/config/moonraker/base/*.conf`
- `/home/pi/printer_data/config/.theme/*`

Генерируется loader-шагами:
- `/home/pi/printer_data/config/moonraker/generated/50-webcam-treed.conf`
  - владелец: `loader/steps/crowsnest-webcam.sh`
- Loader очищает старые `*.conf` в `moonraker/generated` (кроме `00-placeholder.conf`) и затем создает актуальные фрагменты.

Runtime-скрипты камеры:
- source: `runtime-scripts/treed-cam/*`
- deploy: `/home/pi/treed/cam/bin/*`
- владелец: `loader/steps/treed-cam.sh`

Тема KlipperScreen:
- source: `klipperscreen/themes/treed-oled/*`
- deploy: `/home/pi/KlipperScreen/styles/treed-oled/*`
- владелец: `loader/steps/klipperscreen-theme.sh`

## 4. Локальные override-файлы

Шаг `klipper-core` сохраняет и восстанавливает локальные файлы, чтобы не терять ручные настройки на Pi:
- `local_overrides.cfg`
- `mainsail.cfg`
- `timelapse.cfg`
- `crowsnest.conf`
- `KlipperScreen.conf`
- `sonar.conf`

Примечание по Moonraker:
- `moonraker.conf` бэкапится и затем перезаписывается канонической версией из репозитория шагом `moonraker-config`.

## 5. Камера и fail policy

- По умолчанию отсутствие камеры не должно валить базовый provisioning.
- Строгий режим включается переменной `TREED_CAMERA_REQUIRED=1`.

## 6. Что не используется

- В текущей модели нет активной цепочки `root.cfg` / `printer_root.cfg`.
- В текущей модели не используется dynamic profile-switching через `profiles/current`.
