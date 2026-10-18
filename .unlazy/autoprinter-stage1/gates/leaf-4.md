# Component integration

OWNS: moonraker/components/treed_autoprinter.py, moonraker/components/treed_autoprinter_transport.py, tools/tests/test_treed_autoprinter_component.py, moonraker/base/00-core.conf, loader/steps/moonraker-config.sh, moonraker/components/README.md, moonraker/README.md, tools/tests/README.md, docs/autoprinter.md

- [x] G1: Safe component, transport, pairing and delivery integrate without physical actions.
  EVIDENCE: Final component11tests passed; loader bash-n exit0. Enabled and
  disabled lifecycle, receipt lost/invalid reply, queue replay, revoke, bounded
  read1, cancelled download cleanup and SQLite retries covered with fake network.
  Independent reviewer confirmed all findings resolved. Windows asyncio test
  required sandbox escalation for internal socketpair; no external network used.
