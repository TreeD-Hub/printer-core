# Moonraker Components

Папка для кастомных компонентов Moonraker, которые устанавливает лоадер.

Текущий компонент:
- `treed_shell_command.py`

Назначение `treed_shell_command.py`:
- добавляет интеграцию shell_command для вызовов из макросов Klipper;
- используется в связке с командами камеры (`treed_cam_*`).

Деплой:
- выполняет `loader/steps/moonraker-config.sh`;
- целевой путь определяется автоматически по установленному Moonraker.

Важно:
- компонент должен оставаться совместимым с текущей версией Moonraker;
- любые изменения проверять вместе с `moonraker/base/00-core.conf` и `runtime-scripts/treed-cam/*`.

