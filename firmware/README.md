# Firmware Artifacts

Каталог `firmware/` хранит firmware-слой для сборки и ручной прошивки.

## Состав

- `firmware/configs/` — Kconfig-файлы target-ов для auto build шага `loader/steps/firmware-build.sh`.

## Runtime/Deploy контракт (`treed-v2`)

- loader выполняет auto build main+EBB(+Eddy при enabled);
- результат сборки публикуется в staging-каталог:
  - `/home/pi/treed/firmware-artifacts/treed-v2/<run-id>/...`
  - `/home/pi/treed/firmware-artifacts/treed-v2/latest -> <run-id>`
- в `latest` формируются:
  - `manifest.tsv`
  - `checksums.sha256`
  - `build-report.txt`
  - `artifacts/*/*.bin` для STM32 targets
  - `artifacts/*/*.uf2` для RP2040 Eddy target
  - отдельный `artifacts/<target>/klipper.dict` для декодирования именно этой MCU;
- `manifest.tsv` хранит полный Klipper SHA, target config checksum, artifact checksum и dictionary checksum независимо для Octopus, EBB и Eddy.

## Важно

- loader не делает auto-flash;
- прошивка MCU выполняется отдельной операторской командой/процедурой;
- при смене ревизии MCU обновляйте target config или задавайте env override:
  - `TREED_FW_MAIN_CONFIG`
  - `TREED_FW_EBB_CONFIG`
  - `TREED_FW_EDDY_CONFIG`

## Управляемое завершение обновления MCU

1. Проверить `GET /server/treed/update/firmware`: host/build и каждая MCU имеют отдельный статус; `update_required` не является ошибкой сборки.
2. До выбора метода записи физически подтвердить модель и ревизию платы, транспорт, bootloader и его offset. Значение offset не выводится из имени target автоматически.
3. Выбрать только артефакт и `klipper.dict` из одного target-каталога и сверить их SHA-256 с `manifest.tsv`.
4. Получить отдельное подтверждение оператора непосредственно перед записью выбранной платы. Loader и status endpoint запись не выполняют.
5. После каждой платы повторно идентифицировать её через Klippy и снова получить firmware status. Частичный результат остаётся `update_required` для остальных плат; host автоматически не откатывается.
6. После завершения всех плат выполнить отдельно согласованный cold-start/FIRMWARE_RESTART/emergency-stop план без движения и нагрева, затем 60-секундное наблюдение recovery.

Одинаковая строка `v0.13.0` не подтверждает одинаковые исходники. Short SHA принимается только когда он однозначно разрешается в установленном Klipper checkout; dirty/неразрешимое значение остаётся `unknown`. Reported version не заменяет device readback, если плата его не поддерживает.
