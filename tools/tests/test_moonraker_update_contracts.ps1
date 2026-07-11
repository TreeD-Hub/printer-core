$ErrorActionPreference = "Stop"

# ==========================================
# CONTRACT TEST: MOONRAKER TREED UPDATE
# ==========================================
# Назначение:
# - фиксирует fail-closed print guard update API;
# - проверяет оба release target без запуска Moonraker/updater.
# Контур:
# - runnable: использует Python smoke-test с fake Klippy API.

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$pythonCommand = @(
  Get-Command python, python3 -All -ErrorAction SilentlyContinue |
    Where-Object { $_.Source -notlike "*\Microsoft\WindowsApps\*" }
)[0]
if (-not $pythonCommand) {
  throw "python is required"
}

$python = @'
import asyncio
import importlib.util
import pathlib
import sys
import tempfile

repo = pathlib.Path(r"__REPO_ROOT__")
path = repo / "moonraker" / "components" / "treed_update.py"
spec = importlib.util.spec_from_file_location("treed_update", path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class UpdateError(RuntimeError):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


class FakeKlippyApis:
    def __init__(self, print_state):
        self.print_states = list(print_state) if isinstance(print_state, (list, tuple)) else [print_state]
        self.queries = 0

    async def query_objects(self, objects, default=None):
        self.queries += 1
        assert objects == {"print_stats": ["state"]}
        print_state = self.print_states.pop(0) if len(self.print_states) > 1 else self.print_states[0]
        if print_state is None:
            return default
        return {"print_stats": {"state": print_state}}


class FakeServer:
    def __init__(self, print_state):
        self.endpoints = []
        self.klippy = FakeKlippyApis(print_state)

    def register_endpoint(self, *args, **kwargs):
        self.endpoints.append((args, kwargs))

    def lookup_component(self, name):
        assert name == "klippy_apis"
        return self.klippy

    def error(self, message, status_code=400):
        return UpdateError(message, status_code)


class FakeConfig:
    def __init__(self, temp_dir, print_state):
        self.server = FakeServer(print_state)
        self.values = {
            "repo_path": str(temp_dir / "repo"),
            "version_file": str(temp_dir / "VERSION"),
            "shell_manifest_path": str(temp_dir / "manifest.json"),
            "state_file": str(temp_dir / "state.json"),
            "log_file": str(temp_dir / "update.log"),
        }

    def get_server(self):
        return self.server

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeRequest:
    def __init__(self, target_id, target_tag):
        self.values = {"targetId": target_id, "targetTag": target_tag}

    def get(self, key, default=None):
        return self.values.get(key, default)


def available_status():
    return {
        "releaseResults": [
            {"id": "printer-ui", "status": "available", "latestTag": "ui-main-10-1"},
            {"id": "printer-core", "status": "available", "latestTag": "v1.2.3"},
        ]
    }


async def build_component(temp_dir, print_state):
    component = module.TreeDUpdate(FakeConfig(temp_dir, print_state))
    component.release_checks = 0
    component.started = []

    async def check_releases():
        component.release_checks += 1
        return available_status()

    async def start_apply(target_id, target_tag):
        component.started.append((target_id, target_tag))

    component._check_releases = check_releases
    component._start_apply = start_apply
    return component


async def assert_blocked(temp_dir, target_id, target_tag, print_state, status_code):
    component = await build_component(temp_dir, print_state)
    try:
        await component._handle_apply(FakeRequest(target_id, target_tag))
    except UpdateError as error:
        assert error.status_code == status_code
    else:
        raise AssertionError(f"{target_id} must be blocked for print state {print_state!r}")
    assert component.server.klippy.queries == 1
    assert component.release_checks == 0
    assert component.started == []
    assert not component.state_file.exists()


async def assert_allowed(temp_dir, target_id, target_tag):
    component = await build_component(temp_dir, "standby")
    result = await component._handle_apply(FakeRequest(target_id, target_tag))
    assert component.release_checks == 1
    assert component.server.klippy.queries == 2
    assert component.started == [(target_id, target_tag)]
    assert result["busy"] is True


async def assert_race_blocked(temp_dir, target_id, target_tag):
    component = await build_component(temp_dir, ["standby", "printing"])
    try:
        await component._handle_apply(FakeRequest(target_id, target_tag))
    except UpdateError as error:
        assert error.status_code == 409
    else:
        raise AssertionError("print started during release-check must block update")
    assert component.release_checks == 1
    assert component.server.klippy.queries == 2
    assert component.started == []
    assert not component.state_file.exists()


async def main():
    with tempfile.TemporaryDirectory() as temp:
        root = pathlib.Path(temp)
        cases = (("printer-ui", "ui-main-10-1"), ("printer-core", "v1.2.3"))
        for index, (target_id, target_tag) in enumerate(cases):
            for print_state in ("printing", "paused"):
                await assert_blocked(root / f"blocked-{index}-{print_state}", target_id, target_tag, print_state, 409)
            await assert_blocked(root / f"unavailable-{index}", target_id, target_tag, None, 503)
            await assert_race_blocked(root / f"race-{index}", target_id, target_tag)
            await assert_allowed(root / f"allowed-{index}", target_id, target_tag)


asyncio.run(main())
'@

$python = $python.Replace("__REPO_ROOT__", $repoRoot.Replace("\", "\\"))
$python | & $pythonCommand.Source -
if ($LASTEXITCODE -ne 0) {
  throw "Moonraker TreeD update smoke-test failed"
}

Write-Host "Moonraker TreeD update contracts: PASS"
