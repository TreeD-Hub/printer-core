param(
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

# ==========================================
# CONTRACT-TEST: MOONRAKER HOST NETWORK
# ==========================================
# Назначение:
# - фиксирует host-side Wi-Fi contract для TreeD Shell;
# - проверяет Moonraker endpoints, nmcli runtime и loader deploy;
# - защищает границу: здесь нет UI-сортировки и выбора сети.
# Контур:
# - read-only: проверяет только файлы репозитория и pure parser smoke-test.

$ErrorActionPreference = "Stop"

# Блок 1: Общие assert-хелперы для статических контрактов.
function Read-RepoFile {
  param(
    [string]$Path
  )

  return Get-Content -Encoding UTF8 -LiteralPath (Join-Path $RepoRoot $Path) -Raw
}

function Assert-FileExists {
  param(
    [string]$Path,
    [string]$Message
  )

  if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot $Path) -PathType Leaf)) {
    throw "FAIL: $Message ($Path)"
  }
}

function Assert-Contains {
  param(
    [string]$Content,
    [string]$Pattern,
    [string]$Message
  )

  if ($Content -notmatch $Pattern) {
    throw "FAIL: $Message"
  }
}

function Assert-NotContains {
  param(
    [string]$Content,
    [string]$Pattern,
    [string]$Message
  )

  if ($Content -match $Pattern) {
    throw "FAIL: $Message"
  }
}

# Блок 2: Компонент и публичный Moonraker contract.
Assert-FileExists "moonraker/components/treed_host_network.py" "host network Moonraker component must exist"

$component = Read-RepoFile "moonraker/components/treed_host_network.py"
$base = Read-RepoFile "moonraker/base/00-core.conf"
$moonrakerConfigStep = Read-RepoFile "loader/steps/moonraker-config.sh"
$packagesCore = Read-RepoFile "loader/steps/packages-core.sh"
$verify = Read-RepoFile "loader/steps/verify.sh"
$componentsReadme = Read-RepoFile "moonraker/components/README.md"

Assert-Contains $base '(?m)^\[treed_host_network\]\s*$' "base Moonraker config must enable treed_host_network"

foreach ($endpoint in @(
  "/server/treed/network/status",
  "/server/treed/network/scan",
  "/server/treed/network/connect",
  "/server/treed/network/forget"
)) {
  Assert-Contains $component ([regex]::Escape($endpoint)) "component must register $endpoint"
}

Assert-Contains $component 'wrap_result=False' "TreeD Shell expects raw HostNetworkStatus, not Moonraker result wrapper"
Assert-Contains $component 'create_subprocess_exec' "component must run nmcli asynchronously"
Assert-Contains $component 'nmcli' "component must call nmcli"
Assert-Contains $component 'asyncio\.wait_for' "component must bound nmcli communicate with wait_for"
Assert-Contains $component 'process\.terminate\(\)' "component must terminate timed-out nmcli processes"
Assert-Contains $component 'process\.kill\(\)' "component must kill unresponsive nmcli processes"
Assert-Contains $component 'env\["LC_ALL"\] = "C\.UTF-8"' "component must preserve UTF-8 SSIDs"
Assert-Contains $component 'def load_component\(config' "component must expose Moonraker load_component entrypoint"

foreach ($field in @(
  "available",
  "ssid",
  "ipAddress",
  "message",
  "networks",
  "signalPercent",
  "security",
  "saved",
  "connected"
)) {
  Assert-Contains $component ([regex]::Escape('"' + $field + '"')) "HostNetworkStatus field $field must be emitted"
}

foreach ($security in @("open", "wpa2", "wpa3")) {
  Assert-Contains $component ([regex]::Escape('"' + $security + '"')) "security value $security must be supported"
}

# Блок 3: Loader/provisioning contract.
Assert-Contains $moonrakerConfigStep 'SRC_COMPONENT_DIR="\$\{REPO_DIR\}/moonraker/components"' "moonraker-config must deploy components from directory"
Assert-Contains $moonrakerConfigStep 'find "\$\{SRC_COMPONENT_DIR\}"[\s\S]*-name [''"]\*\.py[''"]' "moonraker-config must deploy every repo Moonraker component"
Assert-Contains $packagesCore 'network-manager' "packages-core must install NetworkManager for nmcli"
Assert-Contains $verify 'command -v nmcli' "verify must check nmcli presence"
Assert-Contains $verify '/server/treed/network/status' "verify must check host network status endpoint"
Assert-Contains $verify '"available"' "verify must validate HostNetworkStatus.available"
Assert-Contains $componentsReadme 'treed_host_network\.py' "components README must document host network component"
Assert-NotContains $component 'filterWifiNetworks|getPreferredWifiNetworkId|sort\(' "component must not contain UI filtering/sorting/selection helpers"

# Блок 4: Pure parser smoke-test через сам компонент.
$python = @'
import importlib.util
import asyncio
import pathlib
import sys

repo = pathlib.Path(r"__REPO_ROOT__")
path = repo / "moonraker" / "components" / "treed_host_network.py"
spec = importlib.util.spec_from_file_location("treed_host_network", path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module._split_nmcli_terse_line(r"yes:TreeD\:Lab:87:WPA2") == ["yes", "TreeD:Lab", "87", "WPA2"]
assert module._normalize_security("") == "open"
assert module._normalize_security("WPA3 WPA2") == "wpa3"
assert module._normalize_security("WPA2") == "wpa2"
assert module._strip_cidr("192.168.0.42/24") == "192.168.0.42"


class FakeServer:
    def __init__(self):
        self.endpoints = []

    def register_endpoint(self, *args, **kwargs):
        self.endpoints.append((args, kwargs))

    def error(self, message):
        return RuntimeError(message)


class FakeConfig:
    def __init__(self):
        self.server = FakeServer()

    def get_server(self):
        return self.server


class FakeRequest:
    def __init__(self, ssid, password=None):
        self.ssid = ssid
        self.password = password

    def get_str(self, key):
        assert key == "ssid"
        return self.ssid

    def get(self, key, default=None):
        assert key == "password"
        return self.password if self.password is not None else default


async def run_component_smoke():
    config = FakeConfig()
    component = module.TreeDHostNetwork(config)
    endpoints = {args[0]: kwargs for args, kwargs in config.server.endpoints}
    assert endpoints["/server/treed/network/status"]["wrap_result"] is False
    assert endpoints["/server/treed/network/connect"]["wrap_result"] is False

    calls = []

    async def fake_run(*args, timeout_seconds=module.STATUS_TIMEOUT_SECONDS):
        calls.append((args, timeout_seconds))
        if args[:5] == ("-t", "--escape", "yes", "-f", "DEVICE,TYPE,STATE,CONNECTION"):
            return module.NmcliResult(0, "wlan0:wifi:connected:TreeD Lab\n", "")
        if args[:3] == ("-g", "IP4.ADDRESS", "device"):
            return module.NmcliResult(0, "192.168.0.42/24\n", "")
        if args[:5] == ("-t", "--escape", "yes", "-f", "NAME,TYPE"):
            return module.NmcliResult(0, "TreeD Lab:802-11-wireless\n", "")
        if args[:5] == ("-t", "--escape", "yes", "-f", "ACTIVE,SSID,SIGNAL,SECURITY"):
            return module.NmcliResult(0, "yes:TreeD Lab:87:WPA2\nno:Люкс:64:WPA2\nno::44:WPA2\n", "")
        if args[:4] == ("device", "wifi", "connect", "TreeD Lab"):
            return module.NmcliResult(0, "", "")
        if args == ("connection", "delete", "TreeD Lab"):
            return module.NmcliResult(0, "", "")
        raise AssertionError(args)

    module.shutil.which = lambda name: "nmcli" if name == "nmcli" else None
    component._run_nmcli = fake_run

    status = await component._handle_status(object())
    assert status["available"] is True
    assert status["ssid"] == "TreeD Lab"
    assert status["ipAddress"] == "192.168.0.42"
    assert status["networks"][0]["signalPercent"] == 87
    assert status["networks"][0]["security"] == "wpa2"
    assert status["networks"][1]["ssid"] == "Люкс"
    assert all(network["ssid"] for network in status["networks"])

    scan_status = await component._handle_scan(object())
    assert scan_status["message"] == "scan complete"
    await component._handle_connect(FakeRequest("TreeD Lab", "secret"))
    await component._handle_forget(FakeRequest("TreeD Lab"))
    assert (("-t", "--escape", "yes", "-f", "ACTIVE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes"), module.SCAN_TIMEOUT_SECONDS) in calls
    assert (("device", "wifi", "connect", "TreeD Lab", "password", "secret"), module.CONNECT_TIMEOUT_SECONDS) in calls
    assert (("connection", "delete", "TreeD Lab"), module.FORGET_TIMEOUT_SECONDS) in calls


async def run_timeout_smoke():
    config = FakeConfig()
    component = module.TreeDHostNetwork(config)
    real_wait_for = asyncio.wait_for

    class FakeProcess:
        def __init__(self):
            self.returncode = None
            self.calls = []
            self.killed = False

        async def communicate(self):
            self.calls.append("communicate")
            await asyncio.sleep(10)
            return b"", b""

        def terminate(self):
            self.calls.append("terminate")

        async def wait(self):
            self.calls.append("wait")
            if self.killed:
                return 0
            await asyncio.sleep(10)
            return 0

        def kill(self):
            self.calls.append("kill")
            self.killed = True

    process = FakeProcess()
    wait_for_calls = []

    async def fake_wait_for(awaitable, timeout):
        wait_for_calls.append(timeout)
        return await real_wait_for(awaitable, 0.01)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    module.asyncio.wait_for = fake_wait_for
    module.asyncio.create_subprocess_exec = fake_create_subprocess_exec
    module.shutil.which = lambda name: "nmcli" if name == "nmcli" else None

    result = await component._run_nmcli("device", "wifi", "list", timeout_seconds=30.0)
    assert result.returncode == 124
    assert "timed out" in result.stderr
    assert wait_for_calls == [30.0, 3]
    assert process.calls == ["communicate", "terminate", "wait", "kill", "wait"]


asyncio.run(run_component_smoke())
asyncio.run(run_timeout_smoke())
'@.Replace("__REPO_ROOT__", ($RepoRoot -replace "\\", "\\"))

$python | python -
if ($LASTEXITCODE -ne 0) {
  throw "FAIL: Python parser smoke-test failed"
}

Write-Host "PASS: Moonraker host network contract"
