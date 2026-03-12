# `tools`

Вспомогательные утилиты и проектные ресурсы для проверок и поддержки конфигов.

## Состав

- `tools/validate_klipper_configs.py` — статический валидатор include-цепочки Klipper.
- `tools/skills/` — локальные project-specific skills для Codex в этом репозитории.

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

Содержит project-specific навыки Codex для этого репозитория.

Текущий навык:

- `treed-mainshellos-audit` — read-only аудит по правилам проекта с выводом findings `P0 -> P1 -> P2`.

Общие переиспользуемые навыки вынесены в отдельный репозиторий:

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
