# AutoPrinter core: этап 1

Mode: orchestrated. Revision: 2. CWD: G:/GitHub/printer-core, branch dev.
No commit/push/deploy/SSH/device calls. No physical starts, motion, heat,
Moonraker queue execution or model inference. Feature disabled by default.
Preparation is browser-side; this stage owns durable printer storage, account
binding and queue acceptance/recovery. Backend contract is read-only input at
G:/GitHub/treed-back/docs/autoprinter and its current source.
No new dependency, server framework, public LAN API or secret in UI/logs.
Graph MCP installed and all four repositories indexed; current session uses CLI.
Windows/PowerShell; graph navigation followed by current source verification.
No CHECK execution; approved scoped tests run explicitly and evidence is manual.

| Contract | Outcome | Owner | Gate |
|---|---|---|---|
| C1 | Verify current dev entrypoints, delivery paths and backend wire protocol | 1,2 | N1 |
| C2 | Durable private account-bound asset storage, integrity, crash recovery | root | N2 |
| C3 | Provisioned identity, pairing/revocation and safe outgoing sync | root | N2 |
| C4 | Persisted manifests/revisions/dedup before ack; restart recovery | root | N2 |
| C5 | No possible physical action in stage1; existing standalone flow preserved | root | N3 |
| C6 | Scoped tests, independent review, documentation, no external changes | root | N4 |

| Leaf | Owns | Needs | Tier | Wave | State |
|---|---|---|---|---|---|
| 1 | .unlazy/autoprinter-stage1/gates/leaf-1.md | - | judgment | discovery | VERIFIED |
| 2 | .unlazy/autoprinter-stage1/gates/leaf-2.md | - | judgment | discovery | VERIFIED |
| 3 | moonraker/components/treed_autoprinter_store.py, tools/tests/test_treed_autoprinter_store.py | 1,2 | judgment | implementation | VERIFIED |
| 4 | moonraker/components/treed_autoprinter.py, moonraker/components/treed_autoprinter_transport.py, tools/tests/test_treed_autoprinter_component.py, moonraker/base/00-core.conf, loader/steps/moonraker-config.sh, moonraker/components/README.md, moonraker/README.md, tools/tests/README.md, docs/autoprinter.md | 1,2 | judgment | implementation | VERIFIED |

Parent owns ledgers; both discovery agents read-only. Implementation ownership
and interfaces will be fixed after verified discovery, before implementation.

User amendment: install codebase-memory-mcp and build all four repository graphs
before implementation. Completed; no repository-local graph files generated.

Revision2: user approved install and all-four-repository graphs. Installed verified
upstream0.12.1 and configured Codex MCP; all four indexes built. Current CLI works.
Core generation2026-10-18T10:32:35Z, backend10:33:17Z. Component/loader code coverage
has no recorded issue; 00-core.conf partial and source-read. Fixed interfaces and
ownership in CONTRACT.md. Backend binding_state additive amendment is necessary
integration work; its worker will have separate backend ledger and no core writes.

Final evidence: core full graph refreshed2026-10-18T10:55:06Z (3385nodes/11524edges),
backend11352nodes/52360edges. Store15tests/2skips; component11tests; bash-n0.
Backend20/181 plus correctedfixtureUnit8/98; independent reviewer accepted fixes.
No commit/push/deploy, no production or device execution. Temporary test files
removed; graph cache and pre-install config backup retained intentionally.
