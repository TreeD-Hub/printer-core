# `tools/skills`

Локальный каталог skills для `treed-mainshellOS`.

## Состав

- `treed-mainshellos-audit/` — read-only аудит по проектному контракту.
- `comment-style/` — универсальный skill для проверки/выравнивания формата комментариев.
- `readme-coverage/` — универсальный skill для проверки/заполнения покрытия `README.md`.

## Контракт

- `treed-mainshellos-audit` является project-specific источником аудитного процесса.
- `comment-style` и `readme-coverage` являются локальными копиями общих skills.
- Source of truth для общих skills: `C:/Users/Yawllen/Documents/GitHub/codex-shared-skills`.

## Проверка

Валидировать skill можно скриптом skill-creator:

```bash
python C:/Users/Yawllen/.codex/skills/.system/skill-creator/scripts/quick_validate.py tools/skills/<skill-name>
```
