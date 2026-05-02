# KAMP Vendor (TreeD V2 CoreXY)

Этот каталог содержит vendored snapshot KAMP для профиля `treed_v2_corexy_v1`.

Источник:
- Репозиторий: `https://github.com/kyleisah/Klipper-Adaptive-Meshing-Purging`
- Зафиксированный commit: `b0dad8ec9ee31cb644b94e39d4b8a8fb9d6c9ba0`

Состав snapshot:
- `KAMP_Settings.cfg`
- `Smart_Park.cfg`
- `Line_Purge.cfg`

Контракт интеграции:
- Upstream-файлы в этом каталоге не редактируются вручную.
- Подключение и fail-fast логика находятся в `macros_kamp.cfg`.
- Включены только `Smart_Park` и `Line_Purge` (без adaptive mesh).

Как обновлять snapshot:
1. Скопировать нужные файлы из `Configuration/` upstream-репозитория.
2. Обновить commit в этом README.
3. Проверить, что `macros_kamp.cfg` не требует правок под новый upstream.
4. Запустить `python tools/validate_klipper_configs.py`.
