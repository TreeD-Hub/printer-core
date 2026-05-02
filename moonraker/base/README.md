# Moonraker Base Fragments

`moonraker/base/` содержит статические базовые фрагменты Moonraker (repo-managed слой).

## Runtime-путь

- `${PI_HOME}/printer_data/config/moonraker/base/*.conf`

## Состав

- `00-core.conf`
  - базовый серверный конфиг Moonraker;
  - секции авторизации и update_manager;
  - секция `[update_manager mainsail]` пост-обрабатывается loader-ом: включается только при валидном локальном пути клиента Mainsail (с `release_info.json`);
  - подключение компонента `[treed_shell_command]`;
  - runtime shell_command-команды камеры:
    - `treed_cam_session_start`
    - `treed_cam_snapshot`
    - `treed_cam_session_stop`

## Правила для фрагментов

- порядок применения задается префиксами имен (`00-`, `10-`, ...);
- фрагменты должны оставаться совместимыми с `moonraker/moonraker.conf`;
- для user-зависимых путей использовать шаблоны `{{PI_HOME}}`/`{{PI_USER}}` (подстановка в runtime делает `loader/steps/moonraker-config.sh`);
- generated-конфиги в этот каталог не добавляются (они живут в runtime: `${PI_HOME}/printer_data/config/moonraker/generated/*.conf`, не в репозитории).
