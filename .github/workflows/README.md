# `.github/workflows`

Каталог workflow-файлов GitHub Actions.

Текущие workflow:
- `contracts.yml` — статические и contract checks, адресные тесты updater; PR в `treed-v2` с изменением runtime payload обязан повышать `VERSION`.
- `release.yml` — релиз `printer-core` после изменения `VERSION` в `treed-v2`, по тегу `vX.Y.Z` или вручную. Публикует `treed-core-runtime.zip` и прежние source/manifest assets. Сначала draft с полным набором assets, затем публикация; прерванный draft можно повторить. Версия, тег и commit обязаны совпадать; опубликованные релизы не перезаписываются.

Правило:
- каждый новый workflow должен иметь понятное имя, ограничение по `paths` при возможности и краткое описание в этом файле.
