# KlipperScreen Theme Layer

Папка содержит репозиторные артефакты темы KlipperScreen для TreeD.

Текущая зона ответственности:
- `klipperscreen/themes/treed-oled/style.css` — отдельная тема `treed-oled`.

Деплой:
- шаг: `loader/steps/klipperscreen-theme.sh`
- источник: `klipperscreen/themes/treed-oled`
- runtime-путь: `/home/pi/KlipperScreen/styles/treed-oled`

Переключение темы:
- по умолчанию применяется `treed-oled` (`TREED_KS_THEME=treed-oled`);
- для возврата на сток укажите, например, `TREED_KS_THEME=material-dark`;
- чтобы не менять текущую тему в `KlipperScreen.conf`, используйте `TREED_KS_THEME=keep`.
