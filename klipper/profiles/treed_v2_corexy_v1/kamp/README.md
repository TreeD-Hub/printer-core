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
- Upstream-файлы в этом каталоге являются read-only reference snapshot.
- Активная TreeD-реализация `_TREED_KAMP_SMART_PARK`, `_TREED_KAMP_LINE_PURGE` и `_KAMP_Settings` находится в `../macros_kamp.cfg`.
- Профиль не включает файлы этого каталога напрямую, чтобы purge/park использовали `_TREED_GEOMETRY_CFG`.

Как обновлять snapshot:
1. Скопировать нужные файлы из `Configuration/` upstream-репозитория.
2. Обновить commit в этом README.
3. Сверить, нужны ли изменения в TreeD-реализации `macros_kamp.cfg`.
