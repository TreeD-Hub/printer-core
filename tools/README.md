# `tools`

Вспомогательные утилиты и skills-ресурсы для проверок и поддержки проекта.

## Состав

- `tools/tests/` — локальные контрактные проверки loader/steps.
- `tools/skills/` — локальные skills для Codex, используемые в этом репозитории.

## `tools/skills/`

Содержит навыки Codex:

- `treed-mainshellos-audit` — project-specific аудитный skill.
- `comment-style` — универсальный skill форматирования/проверки комментариев.
- `readme-coverage` — универсальный skill покрытия README.

Для `comment-style` и `readme-coverage` source of truth:

- `C:/Users/Yawllen/Documents/GitHub/codex-shared-skills`

## `tools/tests/`

Содержит легкие контрактные проверки, которые можно запускать локально на Windows без WSL.

Команда:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_loader_contracts.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_klipper_eddy_contracts.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_klipper_parking_contracts.ps1"
```
