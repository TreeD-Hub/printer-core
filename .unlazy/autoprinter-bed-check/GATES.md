# Критерии этапа

- [x] N1: План, bed_check контракт, модель и границы её применения сверены.
  EVIDENCE: Two discovery agents verified current source/release; CONTRACT.md
  fixes candidate occupied/unknown semantics, no empty qualification, pinned E0
  and dependency boundary. User deferred real model runs and installation.
- [x] N2: Локальный провайдер и доставка интегрированы без физического запуска.
  EVIDENCE: E0BedChecker and one-shot CPU runner are wired into explicit
  recheck_bed. New modules are included by existing delivery globs. Component,
  execution and bed checker defaults remain disabled; occupied/unknown never
  authorizes start/completion. No backend, deployment or device state changed.
- [x] N3: Offline проверки и независимое ревью подтверждают защитные границы.
  EVIDENCE: Final execution-module run: 27 tests OK, exit 0 (91.505s).
  Provider/module in prior combined run: 16 passed, 1 Linux-only skip; component
  module passed. Added portable integrity oracle: fresh-process test OK, exit 0
  (0.023s). Prior combined run failed on four execution fixture errors, then
  fixed and rerun as execution-only; no unresolved test failure. RuntimeWarning
  and DeprecationWarning treated as errors. Independent production findings
  closed; parent inspected repaired integrity oracle and tester verified it.
  Real weights, dependencies, camera and hardware not used; full suite not run.
- [x] N4: Документация, граф, очистка и ограничения результата сверены.
  EVIDENCE: docs/autoprinter-bed-check.md describes pinned artifact, default-off,
  CPU dependencies, Linux procfs requirement, wire and qualification limits.
  Graph refreshed 2026-10-18T12:25:17Z: 3772 nodes, 13319 edges; all four Python
  production paths no_recorded_issue/metadata_match. detect_changes impact:
  21 changed paths, 43 impacted symbols (best effort; source/review authoritative).
  git diff --check passed. All agents completed; ownership leases released;
  task test-tmp inventory 0 files/0 bytes, directory removed and absence checked.
  Branch remains dev. No commit, push, deploy, physical or real-model run.
