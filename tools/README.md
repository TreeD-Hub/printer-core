# `tools`

Вспомогательные утилиты репозитория.

Содержимое:
- `validate_klipper_configs.py` — статическая проверка `klipper/printer.cfg` и include-цепочки:
  - отсутствующие include-файлы;
  - пустые glob-include;
  - циклы include;
  - дубли секций.

Запуск:
```bash
python tools/validate_klipper_configs.py
```

Опционально с другим entry:
```bash
python tools/validate_klipper_configs.py --entry klipper/printer.cfg
```
