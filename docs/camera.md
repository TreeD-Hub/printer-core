# Диагностика камеры

Loader устанавливает Crowsnest и Moonraker webcam fragment; камера может быть
необязательной, если `TREED_CAMERA_REQUIRED` не включён. Текущая ожидаемая
настройка TreeD USB-камеры: `1920x1080`, `max_fps: 30`, MJPEG и аппаратный
encoder.

Сверьте применённые параметры Crowsnest и Moonraker:

```bash
grep -R "max_fps\|target_fps\|custom_flags\|1920x1080" -n \
  ~/printer_data/config/crowsnest.conf \
  ~/printer_data/config/moonraker/generated/50-webcam-treed.conf
```

Посмотрите сообщения Crowsnest за текущую загрузку:

```bash
journalctl -u crowsnest -b --no-pager | grep -Ei "fps|resolution|ustreamer|format|encoder|warning|error"
```

Проверьте форматы, которые сообщает подключённая V4L2-камера:

```bash
v4l2-ctl --list-formats-ext -d "$(sed -n 's/^device:[[:space:]]*//p' ~/printer_data/config/crowsnest.conf | tail -n 1)"
```

Ожидаемые значения конфигурации:

```text
resolution: 1920x1080
max_fps: 30
custom_flags: --format=MJPEG --encoder=HW
```
