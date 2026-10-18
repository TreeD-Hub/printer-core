# Core discovery

OWNS: .unlazy/autoprinter-stage1/gates/leaf-1.md

- [x] G1: Active component, loader, persistent paths and relevant checks located.
  EVIDENCE: Explorer and parent verified 00-core.conf include chain, loader copies all component .py files and checks required component list. Persistent state belongs under printer_data/system outside releases/generated. Python 3.13 available. No physical APIs required. Pinned upstream source absent; use existing component entrypoints and stdlib executor transport.
