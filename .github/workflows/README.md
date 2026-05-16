# `.github/workflows`

Каталог workflow-файлов GitHub Actions.

Текущие workflow:
- `release-treed-v2-main.yml` — публикует GitHub Release из push в `treed-v2_main`, считает `vX.Y.Z` по PR labels `major|minor|patch`, берет описание из `## Описание релиза` и добавляет auto-generated changelog.
- `sync-release-labels.yml` — синхронизирует release-label'ы `major|minor|patch` из `.github/labels.json`; запускается вручную и при изменении label-конфига.

Правило:
- каждый новый workflow должен иметь понятное имя, ограничение по `paths` при возможности и краткое описание в этом файле.
