# AutoPrinter execution stage

Mode: orchestrated. Branch: dev. Existing stage1 preserved. No commit, push,
deploy, SSH, device connection, physical start, heat, motion or real inference.
User requests implementation of queue execution, first reconcile plan and current
backend contract. Original attached plans are historical; later user decisions
(browser preparation, printer storage and execution authority) take precedence.
Production activation remains disabled; verification uses offline fakes only.
No CHECK execution; scoped tests explicitly invoked after command inspection.

| Outcome | Acceptance |
|---|---|
| C1 | Current contract, runtime and extension points reconciled before edits |
| C2 | Durable run lifecycle, start intent, dedup, event outbox and restart recovery |
| C3 | Single guarded execution path, real adapter boundary, no actual device calls |
| C4 | Pause/revoke/conflict/uncertain outcome prevent subsequent starts |
| C5 | Fresh bed evidence required; unavailable model and mocks cannot enable live mode |
| C6 | Delivery/docs, scoped offline tests and independent review completed |

| Leaf | Owns | Needs | Tier | Wave | State |
|---|---|---|---|---|---|
| 1 | .unlazy/autoprinter-execution/gates/leaf-1.md | - | judgment | discovery | VERIFIED |
| 2 | .unlazy/autoprinter-execution/gates/leaf-2.md | - | judgment | discovery | VERIFIED |
| 3 | moonraker/components/treed_autoprinter_store.py, moonraker/components/treed_autoprinter_execution.py, tools/tests/test_treed_autoprinter_store.py, tools/tests/test_treed_autoprinter_execution.py | 1,2 | judgment | implementation | VERIFIED |
| 4 | moonraker/components/treed_autoprinter_device.py, tools/tests/test_treed_autoprinter_device.py | 1,2 | judgment | implementation | VERIFIED |
| 5 | moonraker/components/treed_autoprinter.py, tools/tests/test_treed_autoprinter_component.py, moonraker/base/00-core.conf, docs/autoprinter.md, moonraker/components/README.md, tools/tests/README.md, moonraker/README.md | 1,2, interface contract 3/4 | judgment | integration | VERIFIED |

Contract fixed in CONTRACT.md. Pinned upstream references fetched from exact
Moonraker SHA to task reference folder for read-only verification, cleanup at end.
Source baseline HEAD943c4ab3cfbc3f516538646abd7e6f94922db01a, clean dev.

Discovery read-only. Parent owns ledgers; implementation ownership and exact
interfaces assigned after discovery. No agent may create nested agents.
