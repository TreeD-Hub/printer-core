# Pinned runtime evidence

Moonraker source: Arksine/moonraker commit
1cfb0c41e468645951a371621f06d32777b6107c, matching runtime-versions.env.
Fetched only the following public files for source inspection; temporary copies
are removed after acceptance. No printer or running Moonraker was contacted.

| Source under moonraker/components | SHA-256 |
|---|---|
| klippy_apis.py | 2f8e39c92519afb90977ffb75e0b4e1d1803ae24ba1e1aa7430aeb8cf4298b1f |
| job_queue.py | abd06b8226a21ad35054c9a671dfd8dfbe704c8260a76defddda91d2f2b7ccc8 |
| history.py | 942d3f80c4c08d55297f857d09fb516d18906e90010fbf093ced4060def3e294 |
| job_state.py | 71c1647f8fabee4f4ee6678af1e55b75ce21f316b836ed1f3758340591214fe9e |
| file_manager/file_manager.py | a4bc7348613558fe98c282cb9c82b1c8014a3ac48e7223b339bac6f0729b893d |

The adapter uses public Klippy start/pause/cancel/query calls and the configured
gcodes root. Command delivery ACK is not evidence that a print actually started.
Job identity is correlated by immutable per-run filename, not recyclable job IDs.
