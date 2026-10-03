#!/bin/bash

# ==========================================
# БИБЛИОТЕКА LOADER: ПРОГРЕСС УСТАНОВКИ
# ==========================================
# Назначение: единый поток логов и шкала выполненных шагов со счётчиками.
# Контур: presentation-only; не запускает шаги и не меняет их exit status.
# Анимация доступна только в TTY; plain сохраняет читаемый перенаправленный лог.

LOADER_PROGRESS_ACTIVE=0

# Блок 1: Один renderer владеет выводом, чтобы логи не перемешивались со шкалой.
loader_progress_render() {
  local total="$1" animated="$2" columns="$3"
  local completed=0 current=0 warnings=0 errors=0 skipped=0 failed=0
  local step="подготовка" fragment="" pending="" line="" rc=0 finish_rc=1
  local frame=0 frames='|/-\' kind="" value="" result=""
  local LC_ALL=C.UTF-8
  set +e
  trap - ERR EXIT
  trap '' INT TERM

  loader_progress_draw() {
    local percent=$((completed * 100 / total)) width=20 filled=0 bar="" i=0 text=""
    [ "${columns}" -lt 90 ] && width=12
    filled=$((completed * width / total))
    for ((i=0; i<width; i++)); do
      if [ "${i}" -lt "${filled}" ]; then bar+="█"; else bar+="░"; fi
    done
    printf -v text '%s [%s] %3d%% · шаг %d/%d · ошибок %d · предупреждений %d · %s' \
      "${frames:frame:1}" "${bar}" "${percent}" "${current}" "${total}" "${errors}" "${warnings}" "${step}"
    printf '\r\033[2K%s' "${text:0:columns-1}"
    frame=$(((frame + 1) % 4))
  }

  loader_progress_plain() {
    printf '[progress] %d%% · выполнено %d/%d · ошибок %d · предупреждений %d · %s\n' \
      "$((completed * 100 / total))" "${completed}" "${total}" "${errors}" "${warnings}" "${step}"
  }

  loader_progress_log_line() {
    case "$1" in
      *'[WARN]'*|WARNING:*|'W: '*) warnings=$((warnings + 1)) ;;
      *'[ERROR]'*|ERROR:*|'E: '*) errors=$((errors + 1)) ;;
    esac
    printf '%s\n' "$1"
  }

  while true; do
    fragment=""
    IFS= read -r -t 0.15 fragment
    rc=$?
    pending+="${fragment}"
    if [ "${rc}" -gt 128 ]; then
      [ "${animated}" = 1 ] && loader_progress_draw
      continue
    fi
    if [ "${rc}" -ne 0 ] && [ -z "${pending}" ]; then break; fi
    line="${pending}"
    pending=""
    [ "${animated}" = 1 ] && printf '\r\033[2K'

    case "${line}" in
      *$'\036TREED_PROGRESS\t'*)
        # Команда может оставить последнюю строку без newline перед маркером.
        fragment="${line%%$'\036TREED_PROGRESS\t'*}"
        [ -n "${fragment}" ] && loader_progress_log_line "${fragment}"
        IFS=$'\t' read -r kind value result <<< "${line#*$'\036TREED_PROGRESS\t'}"
        case "${kind}" in
          step)
            current="${value}"
            step="${result}"
            [ "${animated}" = 0 ] && loader_progress_plain
            ;;
          done)
            completed=$((completed + 1))
            case "${value}" in
              skipped) skipped=$((skipped + 1)) ;;
              failed) failed=$((failed + 1)) ;;
            esac
            [ "${animated}" = 0 ] && loader_progress_plain
            ;;
          finish)
            finish_rc="${value}"
            break
            ;;
        esac
        ;;
      *)
        loader_progress_log_line "${line}"
        ;;
    esac
    [ "${animated}" = 1 ] && loader_progress_draw
    [ "${rc}" -ne 0 ] && break
  done

  [ "${animated}" = 1 ] && printf '\r\033[2K'
  if [ "${finish_rc}" -ne 0 ] && [ "${errors}" -eq 0 ]; then
    # Некоторые внешние команды завершаются без сообщения об ошибке.
    errors=1
    printf '[ERROR] Loader stopped: step=%s rc=%s\n' "${step}" "${finish_rc}"
  fi
  if [ "${finish_rc}" -eq 0 ] && [ "${completed}" -eq "${total}" ]; then
    result="Завершено"
  else
    result="Остановлено на шаге ${current}/${total}: ${step} (rc=${finish_rc})"
  fi
  printf '[progress] %s · %d%% · выполнено %d/%d · ошибок %d · предупреждений %d · пропущено %d · необязательных сбоев %d\n' \
    "${result}" "$((completed * 100 / total))" "${completed}" "${total}" "${errors}" "${warnings}" "${skipped}" "${failed}"
}

# Блок 2: Подключение после check-mode; стандартный ввод установщика не меняется.
loader_progress_start() {
  local total="$1" mode="${TREED_LOADER_PROGRESS:-auto}" animated=0 columns=120
  [ "${mode}" = off ] && return 0
  if [ "${mode}" = auto ] && [ -t 1 ] && [ "${TERM:-dumb}" != dumb ]; then
    animated=1
    columns="$(tput cols 2>/dev/null || true)"
    case "${columns}" in ''|*[!0-9]*) columns=80 ;; esac
    [ "${columns}" -lt 40 ] && animated=0
  fi
  exec {LOADER_PROGRESS_OUTPUT_FD}>&1 {LOADER_PROGRESS_ERROR_FD}>&2
  exec > >(loader_progress_render "${total}" "${animated}" "${columns}" >&"${LOADER_PROGRESS_OUTPUT_FD}") 2>&1
  LOADER_PROGRESS_PID=$!
  LOADER_PROGRESS_ACTIVE=1
  case "${mode}" in
    auto|plain) ;;
    *) log_warn "Invalid TREED_LOADER_PROGRESS=${mode}; using plain output" ;;
  esac
}

# Блок 3: Маркеры отделены от обычного текста логов управляющим префиксом.
loader_progress_step() {
  [ "${LOADER_PROGRESS_ACTIVE}" = 1 ] || return 0
  printf '\036TREED_PROGRESS\tstep\t%s\t%s\n' "$1" "$2"
}

loader_progress_step_done() {
  [ "${LOADER_PROGRESS_ACTIVE}" = 1 ] || return 0
  printf '\036TREED_PROGRESS\tdone\t%s\n' "$1"
}

# Блок 4: EXIT закрывает renderer даже при fail-fast и сохраняет код loader.
loader_progress_stop() {
  local rc="$1"
  [ "${LOADER_PROGRESS_ACTIVE}" = 1 ] || return 0
  printf '\036TREED_PROGRESS\tfinish\t%s\n' "${rc}" || true
  exec 1>&"${LOADER_PROGRESS_OUTPUT_FD}" 2>&"${LOADER_PROGRESS_ERROR_FD}"
  exec {LOADER_PROGRESS_OUTPUT_FD}>&- {LOADER_PROGRESS_ERROR_FD}>&-
  LOADER_PROGRESS_ACTIVE=0
  wait "${LOADER_PROGRESS_PID}" || true
}
