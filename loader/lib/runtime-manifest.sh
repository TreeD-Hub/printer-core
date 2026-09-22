#!/bin/bash

# ==========================================
# LOADER LIB: RUNTIME MANIFEST
# ==========================================
# Назначение:
# - Загружает единый version manifest runtime-стека TreeD.
# - Fail-fast валидирует immutable refs и checksum до изменения устройства.
# Контур:
# - required для apply/check и version-sensitive step-скриптов.

# Блок 1: Загрузка manifest без сохранения внешних overrides версий.
load_runtime_manifest() {
  local manifest_path="${TREED_RUNTIME_MANIFEST:-${REPO_DIR}/runtime-versions.env}"
  local name=""
  local value=""

  if [ ! -f "${manifest_path}" ]; then
    printf '[runtime-manifest] ERROR: manifest not found: %s\n' "${manifest_path}" >&2
    return 1
  fi

  # shellcheck disable=SC1090
  . "${manifest_path}"

  for name in \
    TREED_RUNTIME_STACK_VERSION \
    TREED_KLIPPER_REPO TREED_KLIPPER_BRANCH TREED_KLIPPER_REF \
    TREED_MOONRAKER_REPO TREED_MOONRAKER_BRANCH TREED_MOONRAKER_REF \
    TREED_KLIPPERSCREEN_REPO TREED_KLIPPERSCREEN_PRIMARY_BRANCH TREED_KLIPPERSCREEN_REF \
    TREED_CROWSNEST_REPO TREED_CROWSNEST_BRANCH TREED_CROWSNEST_REF \
    TREED_MAINSAIL_VERSION TREED_MAINSAIL_ZIP_URL TREED_MAINSAIL_ZIP_SHA256
  do
    value="$(eval "printf '%s' \"\${${name}:-}\"")"
    if [ -z "${value}" ]; then
      printf '[runtime-manifest] ERROR: missing %s in %s\n' "${name}" "${manifest_path}" >&2
      return 1
    fi
  done

  for name in TREED_KLIPPER_REF TREED_MOONRAKER_REF TREED_KLIPPERSCREEN_REF TREED_CROWSNEST_REF; do
    value="$(eval "printf '%s' \"\${${name}}\"")"
    if ! printf '%s' "${value}" | grep -Eq '^[0-9a-f]{40}$'; then
      printf '[runtime-manifest] ERROR: %s must be a full 40-char commit SHA\n' "${name}" >&2
      return 1
    fi
  done

  if ! printf '%s' "${TREED_MAINSAIL_ZIP_SHA256}" | grep -Eq '^[0-9a-f]{64}$'; then
    printf '[runtime-manifest] ERROR: TREED_MAINSAIL_ZIP_SHA256 must be a SHA-256 digest\n' >&2
    return 1
  fi

  export TREED_RUNTIME_MANIFEST="${manifest_path}"
  export TREED_RUNTIME_STACK_VERSION
  export TREED_KLIPPER_REPO TREED_KLIPPER_BRANCH TREED_KLIPPER_REF
  export TREED_MOONRAKER_REPO TREED_MOONRAKER_BRANCH TREED_MOONRAKER_REF
  export TREED_KLIPPERSCREEN_REPO TREED_KLIPPERSCREEN_PRIMARY_BRANCH TREED_KLIPPERSCREEN_REF
  export TREED_CROWSNEST_REPO TREED_CROWSNEST_BRANCH TREED_CROWSNEST_REF
  export TREED_MAINSAIL_VERSION TREED_MAINSAIL_ZIP_URL TREED_MAINSAIL_ZIP_SHA256
}
