# Приёмка Z-bottom и Eddy

`OFFLINE PASS` подтверждает программные проверки. `HARDWARE ACCEPTED` требует
пакетов с принтера и ручного закрытия G2–G7 в [GATES.md](../GATES.md).
Сейчас аппаратная приёмка не выполнена; recovery включён штатно без `enabled`.
Границы профиля: `position_min: -5`, `position_max: 203`,
`bottom_position: 203`, `max_seek: 210`. Отход от фактического DIAG — 5 мм;
после него временная Z равна 198 мм до Eddy Z0.
Слайсер, механический ход и рабочие токи этим процессом не калибруются.

## Исполнители и условия

`loader/steps/runtime-bootstrap.sh` доставляет
`klipper-host/treed_z_recovery.py` в `~/klipper/klippy/extras/`.
Активный профиль включает `z_recovery.cfg` и `probe_eddy_duo.cfg`.
`tools/z_acceptance.py` использует loopback Moonraker и существующий collector
`tools/collect_eddy_diagnostic.sh`. Запускать collector из установленного
`~/treed/printer-core`; он сохраняет исходный diff, runtime config и хеши extra.

Аппаратный запуск выполняет оператор после проверки DIAG и настройки recovery
SGT/тока. Прежний `enabled` в локальном runtime override нужно удалить. До этого
проверить свободный ход стола, отсутствие детали и препятствий. Команды
диагностики требуют ready/idle, запрещены при печати, паузе и активной калибровке.
Настройки температуры и условия измерений должны оставаться одинаковыми;
runner их не меняет, а сохраняет снимки состояний до/после каждой команды.

- `TREED_Z_RECOVERY_TEST CONFIRM=1 [START_Z=...]`: при указанном старте сначала
  движется к нему с известной Z, затем явно сбрасывает homed Z и выполняет одну
  ограниченную sensorless-пробу. Без START_Z начинает из фактического положения.
  Допустим START_Z > 0 и не выше bottom-clearance. Повторных попыток при ошибке нет.
- `TREED_EDDY_ACCEPTANCE_HOME CONFIRM=1`: требует известные XYZ; очищает runtime
  mesh/offsets и выполняет штатные coarse/probe/final_z0 с отметками стадий.
- `TREED_EDDY_ACCEPTANCE_MESH CONFIRM=1`: требует известные XYZ; один обычный
  METHOD=scan, ADAPTIVE=0 в штатной безопасной области. Сетка остаётся активной
  в runtime. Эта диагностика read-only относительно config, но изменяет
  runtime координаты, offsets и mesh; после неё требуется обычная подготовка печати.

## Защита pending autosave

В Klipper `ce7002bed` probe_finalize вызывает set_mesh, затем save_profile,
который вызывает configfile.set даже без SAVE_CONFIG. Диагностический extra
сохраняет оригинальный callback и временно заменяет только save_profile на
no-op. set_mesh остаётся штатным. До замены снимается deepcopy configfile status
(config, settings, save_config_pending, save_config_pending_items).

Общий reentrancy guard запрещает второй диагностический scan. Во время
подавления заблокированы public BED_MESH_CALIBRATE, BASE и обычный Eddy wrapper.
Одноразовый внутренний dispatch допускает ровно один scan. По завершении
снимаются probed_matrix, mesh_matrix, mesh_params, mesh_min/max, z_range;
runtime current mesh остаётся доступным. В finally восстанавливаются точные
callbacks, проверяется identity, сравниваются полные снимки config. Нет
BED_MESH_PROFILE REMOVE, remove_section, SAVE_CONFIG или очистки чужих pending.
Любой сбой восстановления/сравнения даёт fault, shutdown и запрет продолжения.

HTTP timeout/обрыв Moonraker сам по себе не отменяет синхронный G-code в Klipper.
Runner сразу прекращает серию без retry. Extra сохраняет блокировку до
фактического завершения/ошибки scan и возвращает callback в finally. До получения
подтверждения от extra восстановление не считается доказанным; такой пакет
не может получить measured_pass. Принудительное завершение процесса не позволяет
выполнить Python finally; новый процесс загружает штатные callbacks заново.

## Аппаратные серии

Следующие команды двигают принтер; это инструкция оператору, не часть offline tests.
Каждому запуску нужен новый RUN_ID (8–80 ASCII букв, цифр или `_`).

```bash
cd ~/treed/printer-core
TREED_EDDY_RUN_ID=bottom_20260925_01 \
TREED_DIAGNOSTIC_MODE=acceptance TREED_ACCEPTANCE_MODE=bottom \
TREED_ACCEPTANCE_ALLOW_MOTION=1 TREED_ACCEPTANCE_LOSE_Z=1 \
TREED_ACCEPTANCE_RUNS=10 TREED_ACCEPTANCE_STARTS="20 100 190" \
bash tools/collect_eddy_diagnostic.sh
```

Разные STARTS допустимы только при известной исходной Z. Каждый запуск команды
снова теряет homed Z и делает один физический bottom hit; после отказа серия
останавливается без retry. Для G2 нужны минимум 10 успешных циклов из нескольких
START_Z. Без STARTS выполняются циклы из фактических положений, но это не
закрывает требование разных стартов G2. При неизвестной исходной Z первая проба
не имеет общей координатной опоры для сравнения trigger positions.
Для полного bootstrap использовать новый RUN_ID, MODE=bootstrap, ALLOW_MOTION=1
и LOSE_Z=1. Строгий порядок: bottom_reference → x_home → y_home → eddy_coarse →
eddy_probe → final_z0. На первом отказе позднейшие стадии остаются not_started.

```bash
TREED_EDDY_RUN_ID=z0_20260925_01 \
TREED_DIAGNOSTIC_MODE=acceptance TREED_ACCEPTANCE_MODE=z0 \
TREED_ACCEPTANCE_ALLOW_MOTION=1 TREED_ACCEPTANCE_RUNS=10 \
bash tools/collect_eddy_diagnostic.sh

TREED_EDDY_RUN_ID=mesh_20260925_01 \
TREED_DIAGNOSTIC_MODE=acceptance TREED_ACCEPTANCE_MODE=mesh \
TREED_ACCEPTANCE_ALLOW_MOTION=1 TREED_ACCEPTANCE_RUNS=3 \
bash tools/collect_eddy_diagnostic.sh
```

Z0/mesh серии начинают с G28. Для Z0 используется `MCU counter * step_mm -
corrected_z` в одной сессии при непрерывно включённом Z. Это измеренная общая
опора, а не координата, только что принудительно обнулённая макросом. Отключение
мотора инвалидирует сравнение. Критерий `EDDY_Z0_RANGE_MM = 0.05` задан одной
константой в runner. Для mesh численный критерий приёмки пока не задан:
сохраняются три сырые сетки и все попарные разности соответствующих точек.

Cold-start: оператор вручную перезапускает питание/host, не выполняет homing,
затем запускает новый пакет с MODE=cold-start, ALLOW_MOTION=1, LOSE_Z=1.
Повторить минимум для трёх разных boot_id. Автоматического reboot нет.

```bash
python3 tools/z_acceptance.py compare \
  ~/treed/diagnostics/eddy-cold_20260925_01 ~/treed/diagnostics/eddy-cold_20260925_02 \
  ~/treed/diagnostics/eddy-cold_20260925_03 --output /tmp/cold-comparison.json
```

Имена каталогов брать из `TREED_EDDY_DIAG_PACKAGE` (collector использует префикс
`eddy-`). Compare требует полные успешные cold-start пакеты, разные boot_id,
одинаковый commit и runtime config; абсолютные MCU Z0 между загрузками не сравнивает.

## Пакет и переменные

Пакет: `~/treed/diagnostics/eddy-${RUN_ID}`, рядом tar.gz. JSON сохраняется без
текстовых заголовков; exit code и timestamp capture лежат в `.meta`, stderr отдельно.
`result.json` schema_version=1 содержит result, hardware_accepted=false, commands
со временем/состояниями, stages, сырые z_bottom/eddy_z0/mesh/mesh_diagnostics,
статистику, commit, boot_id, Klipper process_id, CAN/MCU deltas и evidence issues.

Для нижней опоры сохранены начальное состояние, одна trigger/halt/overshoot,
причина отказа пробы, SGT/ток, TMC before/after и восстановление. Runner
пересчитывает первую trigger position из известной стартовой Z и MCU-хода;
между циклами учитывает переназначение координаты recovery и требует
непрерывный motor epoch. Это общая командная опора Klipper при условии
отсутствия пропущенных шагов, а не независимое измерение линейкой. Сырые
trigger/halt также сохраняются. Статистика:
min/max/median/range/выборочное standard_deviation сопоставимых trigger
positions и overshoot. Старый `tolerance: 0.5` проверял второй hit одного цикла
и не доказывает независимую повторяемость; численный порог G2 пока утверждает
оператор по аппаратным данным. Диапазон 0.5 мм можно рассмотреть как прежний
ориентир, но не как автоматически принятый допуск. Mesh: median absolute delta, nearest-rank P95, max absolute
delta и RMS по всем попарным точкам при одинаковой геометрии. Сырые матрицы не округляются.

Manifest для mesh содержит `mesh_profile_persistence_suppressed=1`,
`save_profile_restored=1`, `save_config_sent=0`, `pending_config_changed=0`
только при подтверждённых результатах. Нарушение делает пакет непригодным
для приёмки; config не исправляется задним числом. Ошибки связи отделены от
ошибок измерения; пропавший/reset счётчик не считается нулём ошибок.

Дополнительные переменные collector: TREED_ACCEPTANCE_CANDUMP=1 включает candump
на всю серию (по умолчанию 0); TREED_ACCEPTANCE_RUNS по умолчанию 10, диапазон
2..100; TREED_ACCEPTANCE_STARTS — список Z через пробелы, только bottom.
TREED_EDDY_REFERENCE_COMMIT по умолчанию HEAD установленного checkout;
passive остаётся режимом по умолчанию. Прежний единичный режим eddy-scan с
TREED_EDDY_ALLOW_MOTION=1 теперь использует тот же защищённый диагностический extra.

## Offline проверки

```text
python -B tools/tests/test_z_acceptance.py
python -B tools/tests/test_z_recovery.py
python -B tools/tests/run_offline.py
```

Полный runner использует Python, PowerShell 7 (`pwsh`) и Bash; на Windows — Git
Bash. Не подключается к принтеру. Для проверки реального HomingMove дополнительно
задать Z_RECOVERY_KLIPPER_SOURCE на каталог с `extras_homing.py` закреплённого
Klipper; без него один upstream test явно пропускается. Offline успех не
разрешает автономную эксплуатацию и не закрывает аппаратные gates.
