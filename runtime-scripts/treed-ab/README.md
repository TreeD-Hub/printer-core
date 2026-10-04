# TreeD A/B Adapter

`treed-ab` defines the fail-closed status and apply boundary for a future RAUC-backed system update.

- `treed-ab status` reports whether an A/B backend is ready and gives a stable reason code.
- `treed-ab apply <release>` refuses to install while the signed system bundle and post-boot health/rollback path are missing.
- A RAUC binary, system configuration, and attestation alone do not enable updates; bootloader behavior and rollback must be verified and the service worker must implement bundle installation and confirmation.
- System A/B installation is deliberately unavailable. The `printer-core` widget target is now a separate TreeD component package handled by `treed-core-update`; it provides no OS/boot rollback and never calls this adapter.

The script is installed as `/usr/local/sbin/treed-ab` by `moonraker-config.sh`. A future platform package may provide a root-owned `/etc/treed-ab/capability.json` attestation, but it cannot enable updates until the service worker also installs and verifies signed bundles and confirms health after boot.
