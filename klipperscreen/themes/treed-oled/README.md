# `treed-oled`

Тема TreeD для KlipperScreen.

Файлы:
- `style.css` — основной стиль темы.

Как включается:
- loader копирует тему в `${TREED_KLIPPERSCREEN_HOME:-/home/pi/KlipperScreen}/styles/treed-oled`;
- затем выставляет `theme = treed-oled` в `~/printer_data/config/KlipperScreen.conf` (если `TREED_KS_THEME` не равен `keep`).
