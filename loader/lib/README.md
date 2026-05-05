# Loader Lib

Каталог `loader/lib/` содержит общие функции, которые переиспользуются шагами из `loader/steps/`.

## Состав

- `loader/lib/common.sh`
- `loader/lib/rpi.sh`
- `loader/lib/plymouth.sh`

## `common.sh`

Базовая библиотека loader:

- логирование: `log_ts`, `log_info`, `log_warn`, `log_error`;
- проверки и файловые helper-операции: `ensure_root`, `ensure_dir`, `backup_file_once`;
- резолв путей/пользователей:
  - `detect_klipperscreen_home` — путь к KlipperScreen (`WorkingDirectory` -> fallback),
  - `pi_primary_group` — primary group пользователя.

Контракт:

- перед `source` должен быть определен `REPO_DIR`;
- при отсутствии `REPO_DIR` библиотека завершает выполнение с ошибкой.

## `rpi.sh`

Функции для boot-детекта (историческое имя файла сохранено):

- `detect_host_model` / `detect_rpi_model` — best-effort идентификатор платформы.
- `is_mounted` — проверка монтирования каталога.
- `detect_boot_dir` — выбор актуального boot-каталога.
- `detect_boot_backend` — определение backend (`rpi|armbian`).
- `detect_cmdline_file` — поиск `cmdline.txt`.
- `detect_config_file` — поиск `config.txt`.
- `detect_armbian_env_file` — поиск `armbianEnv.txt`.
- `get_armbian_env_value` / `set_armbian_env_value` — безопасное чтение/запись `key=value` в `armbianEnv.txt`.

Используется в шагах, которые правят boot-файлы/cmdline/armbianEnv в host-aware режиме.

## `plymouth.sh`

Функции для контура Plymouth:

- `plymouth_set_default_theme` — установка default-темы;
- `plymouth_rebuild_initramfs` — пересборка initramfs для текущего ядра.

Переменная:

- `PLYMOUTH_THEME_NAME` (по умолчанию `treed`).

## Правила развития библиотеки

- в `lib` держим только переиспользуемую общую логику;
- step-специфичные операции остаются в `loader/steps/*.sh`;
- все функции должны быть совместимы с `set -euo pipefail`;
- новые функции принимают явные входные параметры и корректно обрабатывают ошибки.
