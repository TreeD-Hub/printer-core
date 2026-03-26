# Plymouth Theme: treed

Тема `treed` — текущая активная тема boot splash для TreeD.

## Состав

- `treed.plymouth` — манифест темы (модуль `script`, пути к ресурсам).
- `treed.script` — логика отрисовки splash (логотип + прогресс-бар + fade-out).
- `watermark.png` — логотип/брендинг.
- `prog.png` — текстура полосы прогресса.

## Контракт с loader

Шаг `loader/steps/plymouth-theme-install.sh` ожидает наличие и точные имена:

- `treed.plymouth`
- `treed.script`
- `watermark.png`
- `prog.png`

При отсутствии любого из файлов шаг завершается ошибкой.

## Runtime-деплой

- путь установки: `/usr/share/plymouth/themes/treed`
- `treed.plymouth` и `treed.script` работают относительно этого runtime-пути.

## Поведение `treed.script`

- черный фон;
- логотип по центру;
- прогресс-бар по низу экрана;
- мягкий автопрогресс до 92% до получения реальных событий;
- при завершении boot — доводка до 100% и плавное затухание.
