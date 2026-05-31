# TreeD UI Runtime Commands

Каталог содержит операторские команды управления экранным UI на Rock Pi.

## Состав

- `treed-ui` — переключает активный экранный интерфейс между KlipperScreen (`ks`) и TreeD Shell (`ts`).

## Runtime-пути

- исходник: `runtime-scripts/treed-ui/treed-ui`
- основной путь на устройстве: `/usr/local/sbin/treed-ui`
- пользовательский symlink: `/usr/local/bin/treed-ui`
- состояние выбранного UI: `/etc/default/treed-ui`

## Контракт

- команда требует root-права и при запуске от обычного пользователя пробует перезапуститься через `sudo`;
- выбранный режим сохраняется как `TREED_UI_MODE=ks|ts`;
- при включении `ts` останавливается/отключается `KlipperScreen.service` и запускается `treed-shell.service`;
- при включении `ks` останавливается/отключается `treed-shell.service` и запускается `KlipperScreen.service`.

## Команды

```bash
treed-ui status
treed-ui ts
treed-ui ks
```

Алиасы `TS` и `KS` тоже принимаются.
