"""
MOONRAKER COMPONENT: TREED UPDATE
=================================
Назначение:
- Предоставляет TreeD Printer UI endpoints проверки и применения обновлений.
- Разделяет UI bundle `printer-ui` и системный runtime `printer-core`.
- Сверяет manifest/build с live-версиями required MCU без прошивки.
Контур:
- check/status безопасны и read-only;
- apply запускает root-side updater через ограниченную команду.
"""

from __future__ import annotations

# Блок 1: Импорты и базовые типы.
import asyncio
import csv
import hashlib
import json
import logging
import re
import subprocess
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, TYPE_CHECKING

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper


# Блок 2: Модель release target и константы.
@dataclass
class ReleaseTarget:
    id: str
    label: str
    current_version: str
    release_api_url: str
    tag_prefix: str
    version_scheme: str


SEMVER_RE = re.compile(r"^v?(\d+\.\d+\.\d+)$")
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")
UI_TAG_RE = re.compile(r"^ui-main-\d+-\d+$")
TARGET_ALIASES = {
    "printer-ui": "printer-ui",
    "treed-shell": "printer-ui",
    "printer-core": "printer-core",
    "treed-mainshellos": "printer-core",
}
UPDATE_RUNTIME_OBJECTS: Mapping[str, Optional[List[str]]] = {
    "print_stats": ["state"],
}
FIRMWARE_RUNTIME_OBJECTS: Mapping[str, Optional[List[str]]] = {
    "mcu": ["mcu_version", "mcu_build_versions", "last_stats"],
    "mcu EBBCan": ["mcu_version", "mcu_build_versions", "last_stats"],
    "mcu eddy": ["mcu_version", "mcu_build_versions", "last_stats"],
}
MCU_TARGETS = (
    ("main_octopus", "Octopus", "mcu"),
    ("ebb42_can", "EBBCan", "mcu EBBCan"),
    ("eddy_can", "Eddy", "mcu eddy"),
)
GIT_VERSION_RE = re.compile(r"(?:^|-)g([0-9a-fA-F]{7,40})(-dirty)?$")
PLAIN_SHA_RE = re.compile(r"^([0-9a-fA-F]{7,40})(-dirty)?$")


class TreeDUpdate:
    def __init__(self, config: ConfigHelper) -> None:
        # Блок 3: Конфиг путей, release API и публичных endpoints.
        self.server = config.get_server()
        self.klippy_apis = self.server.lookup_component("klippy_apis")
        self.repo_path = Path(config.get("repo_path", "/home/pi/treed/printer-core"))
        self.version_file = Path(config.get("version_file", str(self.repo_path / "VERSION")))
        self.shell_manifest_path = Path(config.get(
            "shell_manifest_path",
            "/home/pi/treed/treed-shell-runtime/ui/treed-shell-ui-manifest.json",
        ))
        self.state_file = Path(config.get("state_file", "/tmp/treed-update-state.json"))
        self.log_file = Path(config.get("log_file", "/tmp/treed-update-apply.log"))
        self.runtime_manifest_path = Path(config.get(
            "runtime_manifest_path",
            str(self.repo_path / "runtime-versions.env"),
        ))
        self.klipper_repo_path = Path(config.get("klipper_repo_path", "/home/pi/klipper"))
        self.firmware_manifest_path = Path(config.get(
            "firmware_manifest_path",
            "/home/pi/treed/firmware-artifacts/treed-v2/latest/manifest.tsv",
        ))
        self.firmware_observation_file = Path(config.get(
            "firmware_observation_file",
            "/tmp/treed-firmware-observed.json",
        ))
        self.apply_command = config.get("apply_command", "/usr/bin/sudo -n /usr/local/sbin/treed-update-apply")
        self.shell_release_api_url = config.get(
            "printer_ui_release_api_url",
            config.get(
                "shell_release_api_url",
                "https://api.github.com/repos/TreeD-Hub/printer-ui/releases",
            ),
        )
        self.core_release_api_url = config.get(
            "printer_core_release_api_url",
            config.get(
                "mainshell_release_api_url",
                "https://api.github.com/repos/TreeD-Hub/printer-core/releases",
            ),
        )
        self.last_release_results: Optional[List[Dict[str, Any]]] = None

        self.server.register_endpoint(
            "/server/treed/update/status",
            ["GET"],
            self._handle_status,
            wrap_result=False,
        )
        self.server.register_endpoint(
            "/server/treed/update/check",
            ["POST"],
            self._handle_check,
            wrap_result=False,
        )
        self.server.register_endpoint(
            "/server/treed/update/apply",
            ["POST"],
            self._handle_apply,
            wrap_result=False,
        )
        self.server.register_endpoint(
            "/server/treed/update/firmware",
            ["GET"],
            self._handle_firmware_status,
            wrap_result=False,
        )

    async def _handle_status(self, _web_request: object) -> Dict[str, Any]:
        # Блок 4: Локальный статус без сетевого refresh.
        return await self._with_firmware(self._build_status(None))

    async def _handle_check(self, _web_request: object) -> Dict[str, Any]:
        # Блок 5: Refresh release data из GitHub Releases API.
        return await self._with_firmware(await self._check_releases())

    async def _handle_firmware_status(self, _web_request: object) -> Dict[str, Any]:
        return await self._firmware_status()

    async def _handle_apply(self, web_request: object) -> Dict[str, Any]:
        # Блок 6: Запуск update для явно выбранного release target.
        requested_target_id = _request_optional_string(web_request, "targetId") or "printer-core"
        target_id = _normalize_target_id(requested_target_id)
        if target_id is None:
            raise self.server.error(f"unknown update target: {requested_target_id}")

        await self._ensure_apply_allowed()
        current_status = await self._check_releases()
        target = _find_release(current_status["releaseResults"], target_id)
        if target is None:
            raise self.server.error(f"unknown update target: {requested_target_id}")

        requested_tag = _request_optional_string(web_request, "targetTag")
        target_tag = requested_tag or target.get("latestTag")
        target_pattern = UI_TAG_RE if target_id == "printer-ui" else TAG_RE
        if not isinstance(target_tag, str) or target_pattern.match(target_tag) is None:
            raise self.server.error("targetTag does not match the selected update target")

        if target.get("status") != "available":
            return await self._with_firmware(
                self._build_status(f"Обновление {target_id} не требуется.")
            )

        state = self._read_state()
        if state.get("busy") is True:
            return await self._with_firmware(
                self._build_status("Обновление уже выполняется.")
            )

        # Release-check может быть долгим: закрываем гонку со стартом печати.
        await self._ensure_apply_allowed()
        self._write_state({
            "status": "queued",
            "busy": True,
            "message": f"Queued update {target_tag}.",
            "targetId": target_id,
            "targetTag": target_tag,
            "exitCode": 0,
        })
        await self._start_apply(target_id, target_tag)
        return await self._with_firmware(
            self._build_status(f"Запущено обновление {target_tag}.")
        )

    async def _with_firmware(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload["firmware"] = await self._firmware_status()
        return payload

    async def _firmware_status(self) -> Dict[str, Any]:
        # Блок 7: Раздельная сверка manifest, host checkout, build и live MCU.
        captured_at = _utc_now()
        runtime = _read_env_manifest(self.runtime_manifest_path)
        expected_commit = runtime.get("TREED_KLIPPER_REF")
        checkout = await asyncio.to_thread(_git_checkout_status, self.klipper_repo_path)
        build_rows = await asyncio.to_thread(
            _read_firmware_manifest,
            self.firmware_manifest_path,
        )
        try:
            klippy_info = await self.klippy_apis.get_klippy_info(default={})
        except Exception:
            LOGGER.warning("treed_update: Klippy info unavailable", exc_info=True)
            klippy_info = {}
        klippy_info = klippy_info if isinstance(klippy_info, dict) else {}
        klippy_state = str(klippy_info.get("state", "")).lower()
        running_version = str(klippy_info.get("software_version", "")) or None
        objects: Dict[str, Any] = {}
        if klippy_state == "ready":
            try:
                queried = await self.klippy_apis.query_objects(
                    FIRMWARE_RUNTIME_OBJECTS,
                    default={},
                )
            except Exception:
                LOGGER.warning("treed_update: live MCU status unavailable", exc_info=True)
                queried = {}
            if isinstance(queried, dict):
                objects = queried

        cached = _read_json_dict(self.firmware_observation_file)
        cached_mcus = cached.get("mcus", {}) if isinstance(cached.get("mcus"), dict) else {}
        mcus: List[Dict[str, Any]] = []
        fresh_cache: Dict[str, Any] = {}
        for target_id, label, object_name in MCU_TARGETS:
            object_status = objects.get(object_name)
            reported_version = (
                str(object_status.get("mcu_version", "")).strip()
                if isinstance(object_status, dict)
                else ""
            )
            row = build_rows.get(target_id, {})
            mcu_status = _build_mcu_status(
                target_id=target_id,
                label=label,
                object_name=object_name,
                expected_commit=expected_commit,
                reported_version=reported_version,
                klipper_repo_path=self.klipper_repo_path,
                build_row=row,
                captured_at=captured_at,
                reachable=klippy_state == "ready" and isinstance(object_status, dict),
            )
            if reported_version and isinstance(object_status, dict):
                mcu_status["stats"] = object_status.get("last_stats")
                fresh_cache[target_id] = {
                    "reportedVersion": reported_version,
                    "reportedCommit": mcu_status.get("reportedCommit"),
                    "capturedAt": captured_at,
                    "stats": object_status.get("last_stats"),
                }
            else:
                last_known = cached_mcus.get(target_id)
                if isinstance(last_known, dict):
                    mcu_status["lastKnown"] = {**last_known, "stale": True}
            mcus.append(mcu_status)

        if fresh_cache:
            merged_cache = dict(cached_mcus)
            merged_cache.update(fresh_cache)
            try:
                _write_json_atomic(
                    self.firmware_observation_file,
                    {"capturedAt": captured_at, "mcus": merged_cache},
                )
            except OSError:
                LOGGER.warning("treed_update: cannot persist MCU observation", exc_info=True)

        running_commit, running_dirty = _reported_commit(
            running_version,
            self.klipper_repo_path,
        )
        if not expected_commit or not checkout.get("commit") or not running_commit:
            host_status = "unknown"
        elif checkout.get("dirty") is not False or running_dirty:
            host_status = "unknown"
        elif checkout.get("commit") == expected_commit and running_commit == expected_commit:
            host_status = "current"
        else:
            host_status = "update_required"
        host_current = host_status == "current"
        build_current = all(
            item.get("build", {}).get("status") == "current" for item in mcus
        )
        build_update_required = any(
            item.get("build", {}).get("status") == "update_required" for item in mcus
        )
        mcu_states = [str(item.get("status")) for item in mcus]
        if any(state == "unreachable" for state in mcu_states):
            overall = "unreachable"
        elif host_status == "update_required" or build_update_required \
                or any(state == "update_required" for state in mcu_states):
            overall = "update_required"
        elif host_current and build_current and all(state == "current" for state in mcu_states):
            overall = "current"
        else:
            overall = "unknown"

        if host_current and build_current \
                and any(state == "update_required" for state in mcu_states):
            message = "Программная часть обновлена; требуется обновление MCU."
        elif overall == "update_required":
            message = "Требуется обновление host, firmware-артефактов или MCU."
        elif overall == "current":
            message = "Host, артефакты и версии, сообщённые MCU, соответствуют manifest."
        elif overall == "unreachable":
            message = "Одна или несколько MCU недоступны; последнее известное значение помечено как устаревшее."
        else:
            message = "Соответствие firmware не подтверждено."

        return {
            "status": overall,
            "message": message,
            "capturedAt": captured_at,
            "source": "live_klippy_objects" if klippy_state == "ready" else "klippy_unavailable",
            "verification": "reported version and Git identity; not a device binary readback",
            "expectedCommit": expected_commit,
            "host": {
                "installedCheckout": checkout,
                "runningVersion": running_version,
                "runningCommit": running_commit,
                "runningDirty": running_dirty,
                "status": host_status,
            },
            "buildStatus": (
                "current" if build_current
                else "update_required" if build_update_required
                else "unknown"
            ),
            "mcus": mcus,
        }

    async def _ensure_apply_allowed(self) -> None:
        # Apply fail-closed: updater не запускается без достоверного print state.
        try:
            objects = await self.klippy_apis.query_objects(
                UPDATE_RUNTIME_OBJECTS,
                default={},
            )
        except Exception as error:
            raise self.server.error(
                "printer state is unavailable; update apply is blocked",
                503,
            ) from error

        print_stats = objects.get("print_stats") if isinstance(objects, dict) else None
        print_state = (
            str(print_stats.get("state", "")).strip().lower()
            if isinstance(print_stats, dict)
            else ""
        )
        if not print_state:
            raise self.server.error(
                "printer state is unavailable; update apply is blocked",
                503,
            )
        if print_state in {"printing", "paused"}:
            raise self.server.error(
                "updates are unavailable during active print",
                409,
            )

    async def _check_releases(self) -> Dict[str, Any]:
        # Блок 7: Проверка обоих release targets.
        targets = self._build_targets()
        release_results: List[Dict[str, Any]] = []
        for target in targets:
            release_results.append(await self._check_target(target))
        self.last_release_results = release_results
        available_count = sum(result.get("status") == "available" for result in release_results)
        error_count = sum(result.get("status") == "error" for result in release_results)
        missing_labels = [
            str(result.get("label"))
            for result in release_results
            if result.get("status") == "unknown"
        ]
        if error_count:
            message = f"Проверка завершена с ошибками: {error_count}."
        elif available_count:
            message = f"Доступно обновлений: {available_count}."
        elif missing_labels:
            message = f"Release не найден: {', '.join(missing_labels)}."
        else:
            message = "Установлены актуальные версии."
        return self._status_payload(release_results, message)

    def _build_status(self, message: Optional[str]) -> Dict[str, Any]:
        # Блок 8: Status payload из локальных данных и последнего apply state.
        results = self.last_release_results or [
            _unknown_result(target) for target in self._build_targets()
        ]
        return self._status_payload(results, message)

    def _status_payload(
        self,
        release_results: List[Dict[str, Any]],
        message: Optional[str],
    ) -> Dict[str, Any]:
        state = self._read_state()
        is_busy = state.get("busy") is True
        for release in release_results:
            release["canApply"] = release.get("status") == "available" and not is_busy
        can_apply = any(release.get("canApply") is True for release in release_results)

        return {
            "available": True,
            "busy": is_busy,
            "canApply": can_apply,
            "message": message or str(state.get("message") or "Update status ready."),
            "targetId": _normalize_target_id(state.get("targetId")),
            "targetTag": state.get("targetTag"),
            "logPath": str(self.log_file),
            "releaseResults": release_results,
        }

    def _build_targets(self) -> List[ReleaseTarget]:
        return [
            ReleaseTarget(
                id="printer-ui",
                label="TreeD Printer UI",
                current_version=self._read_shell_current_version(),
                release_api_url=self.shell_release_api_url,
                tag_prefix="ui-main-",
                version_scheme="tag",
            ),
            ReleaseTarget(
                id="printer-core",
                label="TreeD Printer Core",
                current_version=self._read_core_version(),
                release_api_url=self.core_release_api_url,
                tag_prefix="v",
                version_scheme="semver",
            ),
        ]

    def _read_core_version(self) -> str:
        if not self.version_file.is_file():
            return "unknown"
        version = self.version_file.read_text(encoding="utf-8").strip()
        return version or "unknown"

    def _read_shell_current_version(self) -> str:
        if not self.shell_manifest_path.is_file():
            return "unknown"
        try:
            manifest = json.loads(self.shell_manifest_path.read_text(encoding="utf-8"))
        except Exception:
            LOGGER.exception("treed_update: failed to read shell manifest")
            return "unknown"

        run_number = manifest.get("runNumber")
        run_attempt = manifest.get("runAttempt")
        if run_number and run_attempt:
            return f"ui-main-{run_number}-{run_attempt}"
        sha = manifest.get("sha")
        return str(sha)[:12] if sha else "unknown"

    async def _check_target(self, target: ReleaseTarget) -> Dict[str, Any]:
        try:
            releases = await asyncio.to_thread(_fetch_releases, target.release_api_url)
            latest_tag = _find_latest_tag(releases, target.tag_prefix)
            if latest_tag is None:
                return _result(target, None, None, "unknown", "Подходящий release не найден.")

            if target.version_scheme == "tag":
                status = "available" if target.current_version != latest_tag else "latest"
                message = "Доступен новый UI bundle." if status == "available" else "Установлен последний UI bundle."
                return _result(target, latest_tag, latest_tag, status, message)

            latest_version = _normalize_semver(latest_tag)
            if latest_version is None:
                raise ValueError(f"Release tag {latest_tag} is not semver")

            status = (
                "available"
                if _compare_semver(latest_version, target.current_version) > 0
                else "latest"
            )
            message = (
                f"Доступно обновление {latest_version}."
                if status == "available"
                else "Установлена актуальная версия."
            )
            return _result(target, latest_tag, latest_version, status, message)
        except Exception as err:
            LOGGER.exception("treed_update: release check failed for %s", target.id)
            return _result(target, None, None, "error", str(err))

    async def _start_apply(self, target_id: str, target_tag: str) -> None:
        command = [*self.apply_command.split(), target_id, target_tag]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        await asyncio.sleep(0.2)
        if process.returncode is not None and process.returncode != 0:
            self._write_state({
                "status": "error",
                "busy": False,
                "message": "Failed to start updater command.",
                "targetId": target_id,
                "targetTag": target_tag,
                "exitCode": process.returncode,
            })
            raise self.server.error("failed to start treed update")

    def _read_state(self) -> Dict[str, Any]:
        if not self.state_file.is_file():
            return {
                "status": "idle",
                "busy": False,
                "message": "Update idle.",
                "targetTag": None,
                "exitCode": 0,
            }
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            LOGGER.exception("treed_update: failed to read state")
            return {
                "status": "error",
                "busy": False,
                "message": "Update state is unreadable.",
                "targetTag": None,
                "exitCode": 1,
            }
        return state if isinstance(state, dict) else {}

    def _write_state(self, state: Dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


# Блок 9: Pure helpers firmware status.
def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_env_manifest(path: Path) -> Dict[str, str]:
    if not path.is_file():
        return {}
    result: Dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    for line in lines:
        match = re.match(r'^([A-Z0-9_]+)="([^"]*)"$', line.strip())
        if match:
            result[match.group(1)] = match.group(2)
    return result


def _read_json_dict(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256_file(path: Path) -> Optional[str]:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _read_firmware_manifest(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8", errors="replace", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
    except (OSError, csv.Error):
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        target = str(row.get("target", ""))
        if target:
            result[target] = dict(row)
    return result


def _git_checkout_status(path: Path) -> Dict[str, Any]:
    if not (path / ".git").exists():
        return {"commit": None, "dirty": None, "source": str(path)}
    commit = _git_output(path, "rev-parse", "HEAD")
    dirty_output = _git_output(path, "status", "--porcelain", "--untracked-files=all")
    return {
        "commit": commit,
        "dirty": None if dirty_output is None else bool(dirty_output),
        "source": str(path),
    }


def _git_output(path: Path, *args: str) -> Optional[str]:
    try:
        process = subprocess.run(
            ["git", "-C", str(path), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if process.returncode != 0:
        return None
    return process.stdout.strip()


def _reported_commit(version: Optional[str], repo_path: Path) -> Tuple[Optional[str], bool]:
    value = (version or "").strip()
    match = GIT_VERSION_RE.search(value) or PLAIN_SHA_RE.match(value)
    if match is None:
        return None, False
    short_commit = match.group(1).lower()
    dirty = bool(match.group(2))
    resolved = _git_output(repo_path, "rev-parse", "--verify", f"{short_commit}^{{commit}}")
    if resolved is None or not re.fullmatch(r"[0-9a-f]{40}", resolved):
        return None, dirty
    return resolved, dirty


def _build_artifact_status(
    row: Mapping[str, Any],
    expected_commit: Optional[str],
) -> Dict[str, Any]:
    if not row:
        return {"status": "unknown"}
    artifact_value = str(row.get("artifact", ""))
    config_value = str(row.get("config", ""))
    dictionary_value = str(row.get("dictionary", ""))
    artifact = Path(artifact_value) if artifact_value else None
    config = Path(config_value) if config_value else None
    dictionary = Path(dictionary_value) if dictionary_value else None
    recorded_artifact_sha = str(row.get("artifact_sha256", ""))
    recorded_config_sha = str(row.get("config_sha256", ""))
    recorded_dictionary_sha = str(row.get("dictionary_sha256", ""))
    checksums_match = bool(
        recorded_artifact_sha
        and recorded_config_sha
        and recorded_dictionary_sha
        and artifact is not None
        and config is not None
        and dictionary is not None
        and _sha256_file(artifact) == recorded_artifact_sha
        and _sha256_file(config) == recorded_config_sha
        and _sha256_file(dictionary) == recorded_dictionary_sha
    )
    source_commit = str(row.get("klipper_commit", "")) or None
    current = bool(expected_commit and source_commit == expected_commit and checksums_match)
    status = (
        "current" if current
        else "update_required" if checksums_match and expected_commit and source_commit
        else "unknown"
    )
    return {
        "status": status,
        "sourceCommit": source_commit,
        "artifact": artifact_value or None,
        "artifactSha256": recorded_artifact_sha or None,
        "config": config_value or None,
        "configSha256": recorded_config_sha or None,
        "dictionary": dictionary_value or None,
        "dictionarySha256": recorded_dictionary_sha or None,
        "checksumsMatch": checksums_match,
    }


def _build_mcu_status(
    *,
    target_id: str,
    label: str,
    object_name: str,
    expected_commit: Optional[str],
    reported_version: str,
    klipper_repo_path: Path,
    build_row: Mapping[str, Any],
    captured_at: str,
    reachable: bool,
) -> Dict[str, Any]:
    reported_commit, dirty = _reported_commit(reported_version, klipper_repo_path)
    if not reachable:
        status = "unreachable"
    elif not reported_version or reported_commit is None or dirty or not expected_commit:
        status = "unknown"
    elif reported_commit == expected_commit:
        status = "current"
    else:
        status = "update_required"
    return {
        "id": target_id,
        "label": label,
        "object": object_name,
        "status": status,
        "expectedCommit": expected_commit,
        "reportedVersion": reported_version or None,
        "reportedCommit": reported_commit,
        "dirty": dirty,
        "capturedAt": captured_at,
        "source": "klippy_object" if reachable else "unavailable",
        "build": _build_artifact_status(build_row, expected_commit),
    }


# Блок 10: Pure helpers release update.
def _request_optional_string(web_request: object, key: str) -> Optional[str]:
    get_value = getattr(web_request, "get")
    value = get_value(key, None)
    if value is None:
        return None
    return str(value).strip() or None


def _fetch_releases(api_url: str) -> List[Dict[str, Any]]:
    request = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "printer-core-update",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = json.load(response)
    if not isinstance(body, list):
        raise ValueError("GitHub Releases returned invalid payload")
    return body


def _find_latest_tag(releases: List[Dict[str, Any]], tag_prefix: str) -> Optional[str]:
    for release in releases:
        if release.get("draft") is True or release.get("prerelease") is True:
            continue
        tag_name = release.get("tag_name")
        if isinstance(tag_name, str) and tag_name.startswith(tag_prefix):
            return tag_name
    return None


def _normalize_semver(value: str) -> Optional[str]:
    match = SEMVER_RE.match(value)
    return match.group(1) if match else None


def _compare_semver(left: str, right: str) -> int:
    if _normalize_semver(right) is None:
        return 1
    left_parts = [int(part) for part in left.split(".")]
    right_parts = [int(part) for part in right.split(".")]
    for left_part, right_part in zip(left_parts, right_parts):
        if left_part != right_part:
            return left_part - right_part
    return 0


def _result(
    target: ReleaseTarget,
    latest_tag: Optional[str],
    latest_version: Optional[str],
    status: str,
    message: str,
) -> Dict[str, Any]:
    return {
        "id": target.id,
        "label": target.label,
        "currentVersion": target.current_version,
        "latestTag": latest_tag,
        "latestVersion": latest_version,
        "status": status,
        "message": message,
        "canApply": False,
    }


def _unknown_result(target: ReleaseTarget) -> Dict[str, Any]:
    return _result(target, None, None, "unknown", "Нет данных.")


def _normalize_target_id(value: object) -> Optional[str]:
    return TARGET_ALIASES.get(value) if isinstance(value, str) else None


def _find_release(releases: List[Dict[str, Any]], release_id: str) -> Optional[Dict[str, Any]]:
    for release in releases:
        if _normalize_target_id(release.get("id")) == release_id:
            return release
    return None


# Блок 11: Entry-point загрузки компонента Moonraker.
def load_component(config: ConfigHelper) -> TreeDUpdate:
    return TreeDUpdate(config)
