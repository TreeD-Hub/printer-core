"""
MOONRAKER COMPONENT: TREED UPDATE
=================================
Назначение:
- Предоставляет TreeD Shell endpoints проверки и применения обновлений.
- Разделяет UI bundle `treed-shell` и системный runtime `treed-mainshellOS`.
Контур:
- check/status безопасны и read-only;
- apply запускает root-side updater через ограниченную команду.
"""

from __future__ import annotations

# Блок 1: Импорты и базовые типы.
import asyncio
import json
import logging
import os
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

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


class TreeDUpdate:
    def __init__(self, config: ConfigHelper) -> None:
        # Блок 3: Конфиг путей, release API и публичных endpoints.
        self.server = config.get_server()
        self.repo_path = Path(config.get("repo_path", "/home/pi/treed/treed-mainshellOS"))
        self.version_file = Path(config.get("version_file", str(self.repo_path / "VERSION")))
        self.shell_manifest_path = Path(config.get(
            "shell_manifest_path",
            "/home/pi/treed/treed-shell-runtime/ui/treed-shell-ui-manifest.json",
        ))
        self.state_file = Path(config.get("state_file", "/tmp/treed-update-state.json"))
        self.log_file = Path(config.get("log_file", "/tmp/treed-update-apply.log"))
        self.apply_command = config.get("apply_command", "/usr/bin/sudo -n /usr/local/sbin/treed-update-apply")
        self.shell_release_api_url = config.get(
            "shell_release_api_url",
            "https://api.github.com/repos/TreeD-Hub/treed-shell/releases",
        )
        self.mainshell_release_api_url = config.get(
            "mainshell_release_api_url",
            "https://api.github.com/repos/TreeD-Hub/treed-mainshellOS/releases",
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

    async def _handle_status(self, _web_request: object) -> Dict[str, Any]:
        # Блок 4: Локальный статус без сетевого refresh.
        return self._build_status(None)

    async def _handle_check(self, _web_request: object) -> Dict[str, Any]:
        # Блок 5: Refresh release data из GitHub Releases API.
        return await self._check_releases()

    async def _handle_apply(self, web_request: object) -> Dict[str, Any]:
        # Блок 6: Запуск update для явно выбранного release target.
        current_status = await self._check_releases()
        target_id = _request_optional_string(web_request, "targetId") or "treed-mainshellos"
        target = _find_release(current_status["releaseResults"], target_id)
        if target is None:
            raise self.server.error(f"unknown update target: {target_id}")

        requested_tag = _request_optional_string(web_request, "targetTag")
        target_tag = requested_tag or target.get("latestTag")
        target_pattern = UI_TAG_RE if target_id == "treed-shell" else TAG_RE
        if not isinstance(target_tag, str) or target_pattern.match(target_tag) is None:
            raise self.server.error("targetTag does not match the selected update target")

        if target.get("status") != "available":
            return self._build_status(f"Обновление {target_id} не требуется.")

        state = self._read_state()
        if state.get("busy") is True:
            return self._build_status("Обновление уже выполняется.")

        self._write_state({
            "status": "queued",
            "busy": True,
            "message": f"Queued update {target_tag}.",
            "targetId": target_id,
            "targetTag": target_tag,
            "exitCode": 0,
        })
        await self._start_apply(target_id, target_tag)
        return self._build_status(f"Запущено обновление {target_tag}.")

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
            "targetId": state.get("targetId"),
            "targetTag": state.get("targetTag"),
            "logPath": str(self.log_file),
            "releaseResults": release_results,
        }

    def _build_targets(self) -> List[ReleaseTarget]:
        return [
            ReleaseTarget(
                id="treed-shell",
                label="TreeD Shell UI",
                current_version=self._read_shell_current_version(),
                release_api_url=self.shell_release_api_url,
                tag_prefix="ui-main-",
                version_scheme="tag",
            ),
            ReleaseTarget(
                id="treed-mainshellos",
                label="TreeD MainShell OS",
                current_version=self._read_mainshell_version(),
                release_api_url=self.mainshell_release_api_url,
                tag_prefix="v",
                version_scheme="semver",
            ),
        ]

    def _read_mainshell_version(self) -> str:
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


# Блок 9: Pure helpers.
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
            "User-Agent": "treed-mainshellOS-update",
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


def _find_release(releases: List[Dict[str, Any]], release_id: str) -> Optional[Dict[str, Any]]:
    for release in releases:
        if release.get("id") == release_id:
            return release
    return None


# Блок 10: Entry-point загрузки компонента Moonraker.
def load_component(config: ConfigHelper) -> TreeDUpdate:
    return TreeDUpdate(config)
