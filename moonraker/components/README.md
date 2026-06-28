# Moonraker Components

Каталог для кастомных компонентов Moonraker, которые деплоятся loader.

## Состав

- `treed_shell_command.py`
- `treed_host_network.py`
- `treed_update.py`

## Назначение `treed_shell_command.py`

- читает секции `[shell_command <name>]` из Moonraker-конфига;
- регистрирует remote method `machine.shell_command`;
- выполняет команды асинхронно через стандартный Moonraker `shell_command` factory;
- безопасно экранирует параметры (`shlex.quote`) перед запуском.

## Назначение `treed_host_network.py`

- регистрирует endpoints Wi-Fi управления для TreeD Shell:
  - `GET /server/treed/network/status`
  - `POST /server/treed/network/scan`
  - `POST /server/treed/network/connect`
  - `POST /server/treed/network/forget`
- вызывает `nmcli` асинхронно через `asyncio.create_subprocess_exec`;
- `scan` ожидает завершения `nmcli --rescan yes`, сохраняет UTF-8 SSID и исключает скрытые сети без имени;
- возвращает raw `HostNetworkStatus` без Moonraker `result` wrapper;
- не содержит UI-правила фильтрации, сортировки или выбора сети.

## Назначение `treed_update.py`

- регистрирует endpoints обновлений для TreeD Shell:
  - `GET /server/treed/update/status`
  - `POST /server/treed/update/check`
  - `POST /server/treed/update/apply`
- проверяет release data отдельно для `treed-shell` и `treed-mainshellOS`;
- применяет выбранный `targetId`: UI tag `ui-main-<run>-<attempt>` или системный semver tag `vX.Y.Z` через root-side `/usr/local/sbin/treed-update-apply`.

## Интеграция

- конфиг-секции объявляются в `moonraker/base/00-core.conf`;
- `[treed_host_network]` требует `network-manager`/`nmcli` на host;
- `[treed_update]` требует deployed `/usr/local/sbin/treed-update-apply` и sudoers-файл из `moonraker-config.sh`;
- текущие команды используются для camera runtime:
  - `treed_cam_session_start`
  - `treed_cam_snapshot`
  - `treed_cam_session_stop`
- скрипты команд лежат в `runtime-scripts/treed-cam/*`.

## Деплой

- выполняет `loader/steps/moonraker-config.sh`;
- путь назначения определяется автоматически по фактической установке Moonraker
  (process path -> systemd unit -> типовые пути -> fallback поиск).
- деплоятся все `*.py` компоненты из этого каталога.

## Ограничения

- компонент должен оставаться совместимым с текущим API Moonraker;
- изменения компонента проверяются вместе с `moonraker/base/00-core.conf`.
