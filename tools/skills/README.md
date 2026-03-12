# `tools/skills`

Локальный каталог project-specific навыков Codex для `treed-mainshellOS`.

## Состав

- `treed-mainshellos-audit/` — read-only аудит по проектному контракту.

## Контракт

- Навыки в этом каталоге описывают только репозиторно-специфичный процесс.
- Общие переиспользуемые навыки и скрипты находятся в отдельном репозитории `C:/Users/Yawllen/Documents/GitHub/codex-shared-skills`.

## Проверка

Валидировать skill можно скриптом skill-creator:

```bash
python C:/Users/Yawllen/.codex/skills/.system/skill-creator/scripts/quick_validate.py tools/skills/<skill-name>
```
