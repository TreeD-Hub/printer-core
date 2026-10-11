# Gates: Адресное чтение резонансов для UI

Scope: только результаты SHAPER_CALIBRATE, без изменения движения, нагрева, auth или deploy.

OWNS: moonraker/components/treed_resonance.py, tools/tests/test_treed_resonance.py, moonraker/base/00-core.conf, moonraker/components/README.md, moonraker/README.md, loader/steps/moonraker-config.sh, tools/tests/test_loader_contracts.ps1

- [x] C1: Endpoint принимает только full/light и возвращает только четыре фиксированных CSV; запрещает symlink, не обычные файлы, слишком большие и меняющиеся файлы; ошибки видимы; независимые адресные проверки пройдены.
  EVIDENCE: Root повторил wsl python3 -B tools/tests/test_treed_resonance.py: 10/10, exit0; reviewer /root/resonance_and_focus_review проверил фиксированные пути, O_NOFOLLOW/regular-file, файловые гонки, CSV/size validation, standard authenticated Moonraker wrapper. Никакого подключения к устройству.
- [x] C2: Компонент доставляется и проверяется существующим loader, конфиг и документация синхронны; команды калибровки и оборудования, auth и deploy не изменены; фактический diff проверен независимо.
  EVIDENCE: Root повторил test_loader_contracts.ps1: 8 исполняемых JSON-сценариев и loader contracts PASS, exit0; WSL bash-n moonraker-config.sh exit0. Reviewer перепроверил actual result.components/failed_components и закрыл P2 false-positive. Конфиг, доставка *.py и docs проверены; deploy не запускался. Чужие изменения вне семи OWNS-путей не включались в приёмку.
