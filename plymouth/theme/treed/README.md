# Plymouth Theme: treed

Тема `treed` — текущая активная тема boot splash для TreeD.

## Состав

- `treed.plymouth` — манифест темы (модуль `script`, пути к ресурсам).
- `treed.script` — ранний splash в оформлении экрана обновления UI: логотип, подписи и полоса ожидания.
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
- Манифест использует LF: парсер Plymouth сохраняет CR в значениях ключей. До первого раздела комментарии идут без пустых строк между ними: пустая строка перед очередным комментарием останавливает чтение разделов.

## Поведение `treed.script`

- чёрный фон и тот же логотип 500x144, что в `printer-ui/src/assets/treed-watermark.png`;
- композиция экрана обновления UI для canvas 960x544: подпись запуска, логотип и статус;
- нижняя полоса ожидания высотой 16px, без фиктивного процента готовности;
- анимация через поддерживаемый `Plymouth.SetRefreshFunction`, без `SetTimer`;
- отдельные подписи для запуска, выключения и системного режима updates;
- последний кадр сохраняется при `plymouth quit --retain-splash` до запуска kiosk.

React-экран не выполняется в initramfs: ранний boot рисует Plymouth.
Изменение `treed.script` требуется доставить в `/usr/share/plymouth/themes/treed`
и включить в initramfs через `loader/steps/plymouth-initramfs.sh`.
Реальную видимость splash и переход в UI проверяют после перезагрузки устройства.
