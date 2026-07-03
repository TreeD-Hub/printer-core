$ErrorActionPreference = "Stop"

# ==========================================
# CONTRACT TEST: MOONRAKER FILAMENT SENSOR
# ==========================================
# Назначение:
# - фиксирует host API уровней чувствительности;
# - проверяет enum, print guard, atomic config и controlled RESTART.
# Контур:
# - runnable: использует Python smoke-test без запущенного Moonraker.

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
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
path = repo / "moonraker" / "components" / "treed_filament_sensor.py"
spec = importlib.util.spec_from_file_location("treed_filament_sensor", path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.SENSITIVITY_MM == {"high": 7.0, "medium": 15.0, "low": 25.0}
assert "detection_length: 7.0" in module._render_runtime_config("high")
assert "[gcode_macro _FILAMENT_SENSOR_SENSITIVITY_STATE]" in module._render_runtime_config("high")
assert 'variable_sensitivity: "high"' in module._render_runtime_config("high")
assert (repo / "klipper" / "filament_motion_runtime.cfg").read_text(encoding="utf-8") == module._render_runtime_config("medium")


class FakeKlippyApis:
    def __init__(self):
        self.print_state = "standby"
        self.restarts = []

    async def query_objects(self, _objects, default=None):
        return {
            "print_stats": {"state": self.print_state},
            "gcode_macro FILAMENT_SENSOR_STATUS": {"mode": "motion"},
            "filament_switch_sensor filament_switch": {"enabled": True, "filament_detected": True},
            "filament_motion_sensor filament_motion": {"enabled": True, "filament_detected": True},
        }

    async def do_restart(self, command):
        self.restarts.append(command)
        return "ok"


class FakeServer:
    def __init__(self):
        self.endpoints = []
        self.klippy = FakeKlippyApis()

    def register_endpoint(self, *args, **kwargs):
        self.endpoints.append((args, kwargs))

    def lookup_component(self, name):
        assert name == "klippy_apis"
        return self.klippy

    def error(self, message, status_code=400):
        return RuntimeError(f"{status_code}: {message}")


class FakeConfig:
    def __init__(self, config_path):
        self.server = FakeServer()
        self.config_path = str(config_path)

    def get_server(self):
        return self.server

    def get(self, key, default=None):
        assert key == "config_path"
        return self.config_path or default


class FakeRequest:
    def __init__(self, action, sensitivity=None):
        self.action = action
        self.sensitivity = sensitivity

    def get_action(self):
        return self.action

    def get_str(self, key):
        assert key == "sensitivity"
        return self.sensitivity


async def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = pathlib.Path(temp_dir) / "filament_motion_runtime.cfg"
        config_path.write_text(module._render_runtime_config("medium"), encoding="utf-8")
        component = module.TreeDFilamentSensor(FakeConfig(config_path))
        endpoint = component.server.endpoints[0]
        assert endpoint[0][0] == "/server/treed/filament-sensor/settings"
        assert endpoint[0][1] == ["GET", "POST"]
        assert endpoint[1]["wrap_result"] is False

        status = await component._handle_settings(FakeRequest("GET"))
        assert status == {
            "available": True,
            "mode": "motion",
            "sensitivity": "medium",
            "presenceDetected": True,
            "switchEnabled": True,
            "motionEnabled": True,
            "restartRequired": False,
            "message": None,
        }

        previous = config_path.read_text(encoding="utf-8")
        try:
            await component._handle_settings(FakeRequest("POST", "invalid"))
        except RuntimeError as error:
            assert "sensitivity must be one of" in str(error)
        else:
            raise AssertionError("invalid sensitivity must fail")
        assert config_path.read_text(encoding="utf-8") == previous

        component.klippy_apis.print_state = "printing"
        try:
            await component._handle_settings(FakeRequest("POST", "high"))
        except RuntimeError as error:
            assert "unavailable during active print" in str(error)
        else:
            raise AssertionError("active print must block sensitivity change")
        assert config_path.read_text(encoding="utf-8") == previous

        component.klippy_apis.print_state = "standby"
        changed = await component._handle_settings(FakeRequest("POST", "high"))
        assert changed["sensitivity"] == "high"
        assert component.klippy_apis.restarts == ["RESTART"]
        assert "detection_length: 7.0" in config_path.read_text(encoding="utf-8")


asyncio.run(main())
'@

$python = $python.Replace("__REPO_ROOT__", $repoRoot.Replace("\", "\\"))
$python | & $pythonCommand.Source -
if ($LASTEXITCODE -ne 0) {
  throw "Moonraker filament sensor smoke-test failed"
}

Write-Host "Moonraker filament sensor contracts: PASS"
