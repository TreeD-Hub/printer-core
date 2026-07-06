# `.github/workflows`

Каталог workflow-файлов GitHub Actions.

Текущие workflow:
- `contracts.yml` — статические проверки shell/Python и PowerShell contract tests для loader/Klipper/Moonraker/runtime-контуров.
- `release.yml` — tag-based релиз `printer-core` по `VERSION` с asset manifest.

Правило:
- каждый новый workflow должен иметь понятное имя, ограничение по `paths` при возможности и краткое описание в этом файле.
