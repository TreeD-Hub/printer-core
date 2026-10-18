# Backend protocol discovery

OWNS: .unlazy/autoprinter-stage1/gates/leaf-2.md

- [x] G1: Exact sync, pairing, receipt, queue and restart DTOs verified in source.
  EVIDENCE: Explorer and parent verified backend DeviceController/DeviceSyncRequest/SyncService/QueueService/StateService and schemas. Epochs start at1; initial boot preserves commands, reboot increments control epoch; binding epoch namespaces storage. Explicit binding_state missing; additive correction required before adapter integration.
