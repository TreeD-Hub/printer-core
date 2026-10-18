# Durable store

OWNS: moonraker/components/treed_autoprinter_store.py, tools/tests/test_treed_autoprinter_store.py

- [x] G1: Durable private storage and queue journal implement the stage1 contract.
  EVIDENCE: Final store15tests passed, 2 Windows symlink tests skipped. Reopen,
  integrity, process ownership, tracked crash cleanup, namespace isolation,
  revision/dedup and revoked recovery covered. Independent reviewer findings
  resolved. No physical API or database/data migration.
