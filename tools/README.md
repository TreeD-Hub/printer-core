# `tools`

Вспомогательные утилиты репозитория для read-only проверок.

## Состав

- `tools/validate_klipper_configs.py` — статический валидатор include-цепочки Klipper.

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
