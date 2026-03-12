# `tools`

Вспомогательные утилиты и skills-ресурсы для проверок и поддержки конфигов.

## Состав

- `tools/validate_klipper_configs.py` — статический валидатор include-цепочки Klipper.
- `tools/skills/` — локальные skills для Codex, используемые в этом репозитории.

## `validate_klipper_configs.py`

Проверяет:

- корректность точки входа (`entry`) и include-цепочки;
- отсутствующие include-файлы;
- пустые glob include;
- include, выходящие за пределы репозитория;
- include-циклы;
- дубли секций (кроме `[include ...]`).

Особенность:

- `local_overrides.cfg` считается опциональным include и может отсутствовать без ошибки.

## `tools/skills/`

Содержит навыки Codex:

- `treed-mainshellos-audit` — project-specific аудитный skill.
- `comment-style` — универсальный skill форматирования/проверки комментариев.
- `readme-coverage` — универсальный skill покрытия README.

Для `comment-style` и `readme-coverage` source of truth:

- `C:/Users/Yawllen/Documents/GitHub/codex-shared-skills`

## Запуск

По умолчанию (`entry=klipper/printer.cfg`):

```bash
python tools/validate_klipper_configs.py
```

С явным entry-файлом:

```bash
python tools/validate_klipper_configs.py --entry klipper/printer.cfg
```

## Формат результата

- успешная проверка: строка `OK: ...`;
- ошибки: `FAIL: ...` + список диагностик с кодами (`E_*`) и ссылкой на `path:line`.
