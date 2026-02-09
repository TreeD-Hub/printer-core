# Moonraker Base Fragments

Здесь лежат базовые фрагменты Moonraker, которые деплоятся как:
- `/home/pi/printer_data/config/moonraker/base/*.conf`

Назначение:
- хранить стабильные, репозиторные секции Moonraker;
- не смешивать их с generated-фрагментами runtime.

Текущий активный фрагмент:
- `00-core.conf` — базовый серверный конфиг и shell_command-интеграция TreeD.

Важно:
- имена и порядок фрагментов задаются префиксами (`00-`, `10-` и т.д.);
- изменения должны быть совместимы с `moonraker/moonraker.conf`.

