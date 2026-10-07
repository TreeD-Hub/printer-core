# Тема KlipperScreen

Папка содержит репозиторные артефакты темы KlipperScreen для TreeD.

## Состав

- `klipperscreen/themes/treed-oled/style.css` — отдельная тема `treed-oled`.
- `klipperscreen/themes/treed-oled/web_ibm_mda.ttf` — шрифт темы `WebPlus IBM MDA`.

## Установка и runtime-пути

- установка KlipperScreen: `loader/steps/klipperscreen-install.sh`;
- шаг: `loader/steps/klipperscreen-theme.sh`
- источник: `klipperscreen/themes/treed-oled`
- runtime-путь: `${TREED_KLIPPERSCREEN_HOME:-${PI_HOME}/KlipperScreen}/styles/treed-oled`
- шрифт: `/usr/local/share/fonts/treed/web_ibm_mda.ttf` (устанавливается тем же шагом).

## Контракт установки

- KlipperScreen является required runtime-компонентом V2;
- managed checkout хранится в `${TREED_KLIPPERSCREEN_HOME:-${PI_HOME}/KlipperScreen}`;
- checkout синхронизируется на точный `TREED_KLIPPERSCREEN_REF` из `runtime-versions.env`, включая переход с более нового коммита; исправный checkout и привязка сервиса к нему позволяют пропустить повторный запуск installer.

## Выбор темы и языка

- по умолчанию применяется `treed-oled` (`TREED_KS_THEME=treed-oled`);
- для возврата на сток укажите, например, `TREED_KS_THEME=material-dark`;
- чтобы не менять текущую тему в `KlipperScreen.conf`, используйте `TREED_KS_THEME=keep`.

Язык интерфейса:

- по умолчанию выставляется русский (`TREED_KS_LANGUAGE=ru`);
- чтобы не менять текущий язык в `KlipperScreen.conf`, используйте `TREED_KS_LANGUAGE=keep`.

Смежная документация: [файлы темы](themes/treed-oled/README.md),
[шаги установки](../loader/steps/README.md),
[переключение экранного UI](../runtime-scripts/treed-ui/README.md).
