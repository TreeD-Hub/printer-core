# Firmware Artifacts

Каталог `firmware/` хранит firmware-слой для сборки и ручной прошивки.

## Состав

- `firmware/configs/` — Kconfig-файлы target-ов для auto build шага `loader/steps/firmware-build.sh`.
- `firmware/rn12/` — legacy артефакт RN12 (не используется в `treed-v2` pipeline).

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

## Важно

- loader не делает auto-flash;
- прошивка MCU выполняется отдельной операторской командой/процедурой;
- при смене ревизии MCU обновляйте target config или задавайте env override:
  - `TREED_FW_MAIN_CONFIG`
  - `TREED_FW_EBB_CONFIG`
  - `TREED_FW_EDDY_CONFIG`
