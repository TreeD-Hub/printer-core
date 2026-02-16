# `.github/workflows`

Каталог workflow-файлов GitHub Actions.

Текущие workflow:
- `klipper-config-check.yml` — проверка include-цепочки и секций Klipper-конфигов через `python tools/validate_klipper_configs.py`.

Правило:
- каждый новый workflow должен иметь понятное имя, ограничение по `paths` при возможности и краткое описание в этом файле.
