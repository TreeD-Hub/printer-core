# KlipperScreen Theme Layer

Папка содержит репозиторные артефакты темы KlipperScreen для TreeD.

Текущая зона ответственности:
- `klipperscreen/themes/treed-oled/style.css` — отдельная тема `treed-oled`.
- `klipperscreen/themes/treed-oled/web_ibm_mda.ttf` — шрифт темы `WebPlus IBM MDA`.

Деплой:
- шаг: `loader/steps/klipperscreen-theme.sh`
- источник: `klipperscreen/themes/treed-oled`
- runtime-путь: `/home/pi/KlipperScreen/styles/treed-oled`
- шрифт: `/usr/local/share/fonts/treed/web_ibm_mda.ttf` (устанавливается тем же шагом).

Переключение темы:
- по умолчанию применяется `treed-oled` (`TREED_KS_THEME=treed-oled`);
- для возврата на сток укажите, например, `TREED_KS_THEME=material-dark`;
- чтобы не менять текущую тему в `KlipperScreen.conf`, используйте `TREED_KS_THEME=keep`.

Язык интерфейса:
- по умолчанию выставляется русский (`TREED_KS_LANGUAGE=ru`);
- чтобы не менять текущий язык в `KlipperScreen.conf`, используйте `TREED_KS_LANGUAGE=keep`.
