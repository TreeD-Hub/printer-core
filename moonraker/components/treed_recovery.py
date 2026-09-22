"""
Moonraker-компонент явного восстановления TreeD V2.

Контур:
- recovery запускается только POST-запросом оператора;
- одна попытка одновременно, без повторов и воспроизведения старого G-code;
- ready подтверждается ограниченным окном свежести всех required MCU.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper


MCU_OBJECTS: Mapping[str, str] = {
    "mcu": "Octopus",
    "mcu EBBCan": "EBBCan",
    "mcu eddy": "Eddy",
}
RECOVERY_OBJECTS: Mapping[str, Optional[List[str]]] = {
    "webhooks": ["state", "state_message"],
    **{
        name: ["mcu_version", "last_stats"]
        for name in MCU_OBJECTS
    },
}
MCU_ERROR_RE = re.compile(r"MCU ['\"]([^'\"]+)['\"]", re.IGNORECASE)
COUNTERS = ("bytes_read", "bytes_retransmit", "bytes_invalid", "tx_retries")


class TreeDRecovery:
    def __init__(self, config: ConfigHelper) -> None:
        # Блок 1: Конфиг, состояние и endpoints.
        self.server = config.get_server()
        self.klippy_apis = self.server.lookup_component("klippy_apis")
        self.eventloop = self.server.get_event_loop()
        self.state_file = Path(config.get(
            "state_file",
            "/tmp/treed-recovery-state.json",
        ))
        self.ready_timeout = float(config.get("ready_timeout", 30))
        self.observe_seconds = float(config.get("observe_seconds", 60))
        self.poll_interval = float(config.get("poll_interval", 1))
        self.stale_seconds = float(config.get("stale_seconds", 10))
        self._monotonic = time.monotonic
        self._sleep = asyncio.sleep
        self._task: Optional[asyncio.Task] = None
        self._klippy_disconnects = 0

        self.server.register_event_handler(
            "server:klippy_disconnect",
            self._handle_klippy_disconnect,
        )

        self.server.register_endpoint(
            "/server/treed/recovery/status",
            ["GET"],
            self._handle_status,
            wrap_result=False,
        )
        self.server.register_endpoint(
            "/server/treed/recovery/start",
            ["POST"],
            self._handle_start,
            wrap_result=False,
        )
        self.server.register_endpoint(
            "/server/treed/recovery/cancel",
            ["POST"],
            self._handle_cancel,
            wrap_result=False,
        )

    async def _handle_status(self, _web_request: object) -> Dict[str, Any]:
        return self._read_state()

    def _handle_klippy_disconnect(self) -> None:
        self._klippy_disconnects += 1

    async def _handle_start(self, _web_request: object) -> Dict[str, Any]:
        # Блок 2: Идемпотентный старт одной явно запрошенной попытки.
        if self._task is not None and not self._task.done():
            return self._read_state()
        attempt_id = secrets.token_hex(8)
        self._transition(
            "recovering",
            "Запрошен FIRMWARE_RESTART; ожидание повторной идентификации MCU.",
            attemptId=attempt_id,
            phase="restart_requested",
            failedMcu=None,
            requestedAction="FIRMWARE_RESTART",
        )
        self._task = self.eventloop.create_task(self._run_recovery(attempt_id))
        return self._read_state()

    async def _handle_cancel(self, _web_request: object) -> Dict[str, Any]:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await self._task
        return self._read_state()

    async def _run_recovery(self, attempt_id: str) -> None:
        # Блок 3: Один restart, ready и 60-секундное окно устойчивости.
        try:
            initial_info = await self.klippy_apis.get_klippy_info(default={})
            initial_message = (
                str(initial_info.get("state_message", ""))
                if isinstance(initial_info, dict)
                else ""
            )
            disconnect_generation = self._klippy_disconnects
            await self.klippy_apis.do_restart("FIRMWARE_RESTART")
            ready_deadline = self._monotonic() + self.ready_timeout
            ready_info: Dict[str, Any] = {}
            restart_progressed = False
            while self._monotonic() < ready_deadline:
                info = await self.klippy_apis.get_klippy_info(default={})
                ready_info = info if isinstance(info, dict) else {}
                ready_state = str(ready_info.get("state", "")).lower()
                ready_message = str(ready_info.get("state_message", ""))
                if ready_state == "ready" and (
                    restart_progressed
                    or self._klippy_disconnects > disconnect_generation
                ):
                    break
                if ready_state not in {"", "shutdown", "error"}:
                    restart_progressed = True
                if "webhooks" in ready_message.lower() \
                        and (restart_progressed or ready_message != initial_message):
                    self._transition(
                        "emergency_stop",
                        ready_message,
                        attemptId=attempt_id,
                        phase="cancelled_by_emergency_stop",
                        failedMcu=None,
                    )
                    return
                await self._sleep(self.poll_interval)
            else:
                self._fail(
                    attempt_id,
                    str(ready_info.get("state_message") or "Klippy did not become ready"),
                )
                return

            self._transition(
                "observing",
                "Связь восстановлена; идёт проверка устойчивости MCU.",
                attemptId=attempt_id,
                phase="stability_window",
                readyAt=_utc_now(),
            )
            started = self._monotonic()
            last_values: Dict[str, Dict[str, int]] = {}
            last_fresh = {name: started for name in MCU_OBJECTS}
            baseline: Dict[str, Dict[str, int]] = {}
            latest: Dict[str, Dict[str, int]] = {}
            reidentified_mcus: List[str] = []
            last_successful: Dict[str, Dict[str, Any]] = {}

            while self._monotonic() - started < self.observe_seconds:
                try:
                    objects = await self.klippy_apis.query_objects(
                        RECOVERY_OBJECTS,
                        default={},
                    )
                except Exception as error:
                    self._fail(
                        attempt_id,
                        f"MCU status query unavailable: {error}",
                        counter_deltas=_counter_deltas(baseline, latest),
                        reidentified_mcus=reidentified_mcus,
                        last_successful=last_successful,
                    )
                    return
                if not isinstance(objects, dict):
                    self._fail(
                        attempt_id,
                        "MCU status query unavailable",
                        counter_deltas=_counter_deltas(baseline, latest),
                        reidentified_mcus=reidentified_mcus,
                        last_successful=last_successful,
                    )
                    return
                webhooks = objects.get("webhooks")
                state = (
                    str(webhooks.get("state", "")).lower()
                    if isinstance(webhooks, dict)
                    else ""
                )
                if state != "ready":
                    message = (
                        str(webhooks.get("state_message", ""))
                        if isinstance(webhooks, dict)
                        else "Klippy status unavailable"
                    )
                    if "webhooks" in message.lower():
                        self._transition(
                            "emergency_stop",
                            message or "Recovery прерван аварийной остановкой.",
                            attemptId=attempt_id,
                            phase="cancelled_by_emergency_stop",
                            failedMcu=None,
                            counterDeltas=_counter_deltas(baseline, latest),
                            reidentifiedMcus=reidentified_mcus,
                            lastSuccessfulCommunication=last_successful,
                        )
                        return
                    self._fail(
                        attempt_id,
                        message,
                        counter_deltas=_counter_deltas(baseline, latest),
                        reidentified_mcus=reidentified_mcus,
                        last_successful=last_successful,
                    )
                    return

                now = self._monotonic()
                for object_name, label in MCU_OBJECTS.items():
                    mcu = objects.get(object_name)
                    if isinstance(mcu, dict) and mcu.get("mcu_version"):
                        if label not in reidentified_mcus:
                            reidentified_mcus.append(label)
                        last_successful[label] = {
                            "capturedAt": _utc_now(),
                            "secondsSinceReady": round(now - started, 3),
                        }
                for object_name, label in MCU_OBJECTS.items():
                    mcu = objects.get(object_name)
                    if not isinstance(mcu, dict) or not mcu.get("mcu_version"):
                        self._fail(
                            attempt_id,
                            f"Lost communication with MCU '{label}'",
                            label,
                            _counter_deltas(baseline, latest),
                            reidentified_mcus,
                            last_successful,
                        )
                        return
                    counters = _integer_counters(mcu.get("last_stats"))
                    if "bytes_read" not in counters:
                        self._fail(
                            attempt_id,
                            f"Freshness data unavailable for MCU '{label}'",
                            label,
                            _counter_deltas(baseline, latest),
                            reidentified_mcus,
                            last_successful,
                        )
                        return
                    if object_name not in baseline:
                        baseline[object_name] = counters
                    previous = last_values.get(object_name)
                    if previous is not None and any(
                        key in previous and key in counters and counters[key] < previous[key]
                        for key in COUNTERS
                    ):
                        latest[object_name] = counters
                        self._fail(
                            attempt_id,
                            f"Counters reset for MCU '{label}' during observation",
                            label,
                            _counter_deltas(baseline, latest),
                            reidentified_mcus,
                            last_successful,
                        )
                        return
                    if previous is None or counters["bytes_read"] != previous.get("bytes_read"):
                        last_fresh[object_name] = now
                    elif now - last_fresh[object_name] >= self.stale_seconds:
                        latest[object_name] = counters
                        self._fail(
                            attempt_id,
                            f"Stale data from MCU '{label}'",
                            label,
                            _counter_deltas(baseline, latest),
                            reidentified_mcus,
                            last_successful,
                        )
                        return
                    last_values[object_name] = counters
                    latest[object_name] = counters
                await self._sleep(self.poll_interval)

            self._transition(
                "stable",
                "MCU оставались доступными в течение окна наблюдения.",
                attemptId=attempt_id,
                phase="complete",
                completedAt=_utc_now(),
                counterDeltas=_counter_deltas(baseline, latest),
                reidentifiedMcus=reidentified_mcus,
                lastSuccessfulCommunication=last_successful,
            )
        except asyncio.CancelledError:
            self._transition(
                "cancelled",
                "Recovery отменён оператором или новой аварийной остановкой.",
                attemptId=attempt_id,
                phase="cancelled",
            )
        except Exception as error:
            self._fail(attempt_id, str(error))

    def _fail(
        self,
        attempt_id: str,
        message: str,
        failed_mcu: Optional[str] = None,
        counter_deltas: Optional[Dict[str, Dict[str, Optional[int]]]] = None,
        reidentified_mcus: Optional[List[str]] = None,
        last_successful: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        failed_mcu = failed_mcu or _failed_mcu_from_message(message)
        self._transition(
            "failed",
            message or "Recovery failed",
            attemptId=attempt_id,
            phase="failed",
            failedMcu=failed_mcu,
            failedAt=_utc_now(),
            counterDeltas=counter_deltas,
            reidentifiedMcus=reidentified_mcus or [],
            lastSuccessfulCommunication=last_successful or {},
        )

    # Блок 4: Состояние с историей причин без маскировки нового сбоя.
    def _read_state(self) -> Dict[str, Any]:
        try:
            value = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {
                "status": "idle",
                "busy": False,
                "message": "Recovery не запускался.",
                "history": [],
            }
        return value if isinstance(value, dict) else {}

    def _transition(self, status: str, message: str, **extra: Any) -> None:
        previous = self._read_state()
        history = previous.get("history", [])
        history = list(history) if isinstance(history, list) else []
        if previous.get("status") not in {None, "idle"}:
            history.append({
                "status": previous.get("status"),
                "message": previous.get("message"),
                "failedMcu": previous.get("failedMcu"),
                "attemptId": previous.get("attemptId"),
                "phase": previous.get("phase"),
                "requestedAction": previous.get("requestedAction"),
                "recordedAt": previous.get("updatedAt"),
            })
        payload = {
            "status": status,
            "busy": status in {"recovering", "observing"},
            "message": message,
            "updatedAt": _utc_now(),
            "history": history[-20:],
            **extra,
        }
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.state_file)

    def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _integer_counters(value: object) -> Dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        key: int(value[key])
        for key in COUNTERS
        if isinstance(value.get(key), (int, float))
    }


def _counter_deltas(
    before: Mapping[str, Mapping[str, int]],
    after: Mapping[str, Mapping[str, int]],
) -> Dict[str, Dict[str, Optional[int]]]:
    result: Dict[str, Dict[str, Optional[int]]] = {}
    for object_name, label in MCU_OBJECTS.items():
        result[label] = {}
        for counter in COUNTERS:
            old = before.get(object_name, {}).get(counter)
            new = after.get(object_name, {}).get(counter)
            result[label][counter] = None if old is None or new is None else new - old
    return result


def _failed_mcu_from_message(message: str) -> Optional[str]:
    match = MCU_ERROR_RE.search(message)
    return match.group(1) if match else None


def load_component(config: ConfigHelper) -> TreeDRecovery:
    return TreeDRecovery(config)
