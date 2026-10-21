# `.github/workflows`

GitHub Actions: автоматические проверки и публикация артефактов `printer-core`.

Текущие workflow:

- `contracts.yml` — синтаксис, полный offline-набор и запуск собранного runtime на Moonraker из закреплённого SHA. Повышать версию в PR не требуется.
- `release.yml` — релиз `printer-core` после push в `treed-v2` или ручного запуска с автоматической версией `0.<run_number>.<run_attempt>`, как в UI. Тег имеет вид `v0.<run_number>.<run_attempt>`. При push ручного тега `vX.Y.Z` версия должна совпадать с `VERSION` этого commit. Публикует `treed-core-runtime.zip` и прежние source/manifest assets. Сначала draft с полным набором assets, затем публикация. Опубликованные релизы не перезаписываются.

Автоматическая версия записывается в `VERSION` только в checkout release job и в source asset. Runtime и release manifests получают ту же версию. Workflow не коммитит и не пушит изменения `VERSION` в ветку. Повторный запуск получает новую попытку и новый тег, прерванный draft остаётся для своего тега.

Оба workflows запускают `tools/tests/run_offline.py` и `check_moonraker_startup.py`.
Проверка запуска использует модули и конфиги из runtime ZIP, настоящий Moonraker
и локальный имитатор Klippy: все обязательные компоненты должны загрузиться,
API — ответить, readiness — пройти несколько раз подряд. Проверяются новый
конфиг, старый без `vision_socket` и отказ при отсутствующей обязательной опции.
Release публикует тот же runtime ZIP, который прошёл эту проверку.
Физические MCU, движение, нагрев, камера и ML-модели требуют проверки на устройстве.

Правило:

- каждый новый workflow должен иметь понятное имя, ограничение по `paths` при возможности и краткое описание в этом файле.

Смежная документация: [локальные проверки](../../tools/tests/README.md),
[пакет обновления Core](../../runtime-scripts/treed-update/README.md).
