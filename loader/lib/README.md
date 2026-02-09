# Loader Libraries

Каталог `loader/lib/` содержит переиспользуемые функции для шагов loader.

## Файлы

- `common.sh`
- `rpi.sh`
- `plymouth.sh`

## `common.sh`

Базовые утилиты:

- логирование: `log_info`, `log_warn`, `log_error`;
- проверки: `ensure_root`;
- filesystem helpers: `ensure_dir`, `backup_file_once`;
- пользователи/группы: `pi_primary_group`.

Требование:

- при source файла должен быть задан `REPO_DIR`, иначе скрипт завершится с ошибкой.

## `rpi.sh`

Функции детекта платформы и boot-путей:

- `detect_rpi_model`
- `detect_boot_dir`
- `detect_cmdline_file`
- `detect_config_file`

Используется шагами, которые работают с `config.txt` и `cmdline.txt`.

## `plymouth.sh`

Хелперы для Plymouth:

- `plymouth_set_default_theme`
- `plymouth_rebuild_initramfs`

Управляющая переменная:

- `PLYMOUTH_THEME_NAME` (по умолчанию `treed`).

## Правила для изменений

- не дублировать логику из `steps` в библиотеках;
- добавлять только обобщаемые функции;
- сохранять совместимость с `set -euo pipefail`;
- для новых функций использовать явные входные параметры и проверку ошибок.
