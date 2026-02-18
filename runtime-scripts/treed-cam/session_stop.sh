#!/bin/bash
set -euo pipefail

# ==========================================
# RUNTIME SCRIPT: TREED CAM SESSION STOP
# ==========================================
# Назначение:
# - Завершает активную сессию TreeD Cam.
# - Удаляет marker-файл текущей сессии.
# Контур:
# - идемпотентный (повторный вызов безопасен).

# Блок 1: Очистка session-marker файла.
rm -f /tmp/treed_cam_session_dir
