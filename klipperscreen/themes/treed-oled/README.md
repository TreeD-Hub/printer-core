# `treed-oled`

Тема TreeD для KlipperScreen.

Файлы:
- `style.stock-material-dark.css` — эталонная копия стоковой темы `material-dark` (read-only, не редактируется).
- `style.css` — рабочая тема `treed-oled`: стоковая база + блок `TreeD overrides` в конце файла.

Правило правок:
- любые визуальные изменения вносить только в `style.css`;
- `style.stock-material-dark.css` использовать только для сравнения структуры/цветов с оригиналом.

Иконки:
- `images/` в репозитории не дублируются;
- loader при деплое подтягивает fallback icon-pack из установленной стоковой темы KlipperScreen.

Как включается:
- loader копирует тему в `${TREED_KLIPPERSCREEN_HOME}/styles/treed-oled`;
- затем выставляет `theme = treed-oled` в `~/printer_data/config/KlipperScreen.conf` (если `TREED_KS_THEME` не равен `keep`).
