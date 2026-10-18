# AutoPrinter: исправления независимого review

Mode Orchestrated, явный astra вызов пользователя сохраняется. Главные gates:
G:/GitHub/treed-back/.unlazy/autoprinter-completion/GATES.md.
Baseline dev bd25cb69cd49af980d863635102ffcfd11432689.
Никаких физических запусков, ML, сети, runtime, build/full suite или Git write
worker-агентом. Root принимает результат, назначает ближайшие offline tests,
делает один отдельный commit и возвращает исправления независимому reviewer.

| Leaf | Owns | Needs | Tier | Wave | State |
|---|---|---|---|---|---|
| 10 | moonraker/components/treed_autoprinter_execution.py, moonraker/components/treed_autoprinter_store.py, tools/tests/test_treed_autoprinter_execution.py, tools/tests/test_treed_autoprinter_store.py, docs/autoprinter.md, docs/autoprinter-todo.md | review findings P1/P2 | fix | core-review-fixes | VERIFIED |

P1: local stop_after_current должен сохраняться после старого remote resume и
рестарта. Использовать существующий durable local_pause latch для локального
запрета следующего старта; remote resume не очищает локальный запрет и при нём
не снимает stop_after_current. Только явный local resume снимает local stop.
Текущий run продолжает reconcile/completion; не превращать это в pause print.
Не добавлять wire/meta schema без необходимости, не обходить bed gate.

P2: reorder_queue payload перечисляет только queued без start_intent. Проверять
полное множество именно eligible queued, сохранять slots всех остальных runs,
дедуп/epoch/revision/replay. Не допускать пропуск/дубли/чужие/активные IDs.

Отдельный риск max_runs включает history binding; не удалять историю/commands
ради снятия лимита. Записать TODO согласования retention/capacity и fail-closed
поведения без автоматического cleanup. Общий lock/network задержки уже описаны.
