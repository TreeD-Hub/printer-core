"""Назначение: запуск доставленного runtime на закреплённом Moonraker перед релизом.
Контур: временный каталог, localhost и имитатор Klippy; без systemd и оборудования.
"""

import argparse
import asyncio
import configparser
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.request import ProxyHandler, build_opener


ROOT = Path(__file__).resolve().parents[2]
core = SourceFileLoader("treed_core_startup", str(
    ROOT / "runtime-scripts/treed-update/treed-core-update")).load_module()


# Блок 1: Имитируется только протокол оборудования; Moonraker и компоненты настоящие.
async def klippy_client(reader, writer, home):
    objects = {
        "webhooks": {"state": "ready", "state_message": "CI Klippy fixture"},
        "print_stats": {"state": "standby", "filename": "", "total_duration": 0,
                        "print_duration": 0, "filament_used": 0, "message": ""},
        "virtual_sdcard": {"is_active": False, "progress": 0, "file_position": 0},
        "display_status": {"progress": 0, "message": None},
        "pause_resume": {"is_paused": False},
        "configfile": {"config": {"virtual_sdcard": {"path": str(home / "printer_data/gcodes")}}},
        "heaters": {"available_sensors": [], "available_heaters": []},
        "gcode_macro _TREED_CAM_STATE": {"enabled": 0, "generation": 0},
        "gcode_macro _TREED_OPERATION_STATE": {"phase": "idle"},
    }
    try:
        while True:
            request = json.loads((await reader.readuntil(b"\x03"))[:-1])
            method, params = request["method"], request.get("params", {})
            if method == "info":
                result = {"state": "ready", "state_message": "CI Klippy fixture",
                          "software_version": "ci-fixture", "klipper_path": str(home / "klipper"),
                          "python_path": sys.executable}
            elif method == "list_endpoints":
                result = {"endpoints": ["info", "objects/list", "objects/query", "objects/subscribe"]}
            elif method == "objects/list":
                result = {"objects": list(objects)}
            elif method in {"objects/query", "objects/subscribe"}:
                result = {"eventtime": time.monotonic(), "status": {
                    name: objects.get(name, {}) for name in params.get("objects", {})}}
            elif method in {"gcode/subscribe_output", "register_remote_method", "gcode/help"}:
                result = {}
            else:
                raise AssertionError(f"Unexpected Klippy command during startup: {method}")
            writer.write(json.dumps({"id": request["id"], "result": result}).encode() + b"\x03")
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()


def get_json(endpoint):
    with build_opener(ProxyHandler({})).open("http://127.0.0.1:7125" + endpoint, timeout=2) as response:
        return json.load(response)


# Блок 2: Конфиги и модули берутся из того же ZIP, который будет опубликован.
def deploy_fixture(home, installed, payload, scenario):
    env = {"TREED_UPDATE_PI_HOME": str(home), "TREED_UPDATE_PI_USER": "ci"}
    config_dir = home / "printer_data/config"
    for key, data in payload.items():
        if key == "config/moonraker.conf" or key.startswith("config/moonraker/base/"):
            path = home / "printer_data" / key
            path.parent.mkdir(parents=True, exist_ok=True)
            # Как при update без опциональных Mainsail/Crowsnest: renderer сохраняет их отсутствие.
            path.write_bytes(core.render(key, data, b"[server]\n", env))
        elif key.startswith("moonraker/"):
            (installed / "moonraker/components" / Path(key).name).write_bytes(data)
    base = config_dir / "moonraker/base/00-core.conf"
    config = configparser.ConfigParser(interpolation=None)
    config.read(base, encoding="utf-8")
    required = {name for name in config.sections() if name.startswith("treed_")}
    loader = (ROOT / "loader/steps/moonraker-config.sh").read_text(encoding="utf-8")
    match = re.search(r"local required_components=\(([^)]+)\)", loader)
    assert match and set(match[1].split()) <= required, "Runtime omits required loader components"
    option = {"legacy": "vision_socket", "invalid": "frames_root"}.get(scenario)
    if option:
        config.remove_option("treed_detection", option)
        with base.open("w", encoding="utf-8") as output:
            config.write(output)
    generated = config_dir / "moonraker/generated"
    generated.mkdir(parents=True)
    (generated / "00-ci.conf").write_text(
        "[machine]\nprovider: none\nvalidate_service: False\nvalidate_config: False\n"
        "[update_manager]\nenable_auto_refresh: False\nenable_system_updates: False\n",
        encoding="utf-8")
    for relative in ("printer_data/comms", "printer_data/gcodes", "klipper", "treed/cam/prints"):
        (home / relative).mkdir(parents=True, exist_ok=True)
    return required


async def run_scenario(directory, installed, payload, scenario):
    home = directory / scenario
    required = deploy_fixture(home, installed, payload, scenario)
    socket_path = home / "printer_data/comms/klippy.sock"
    server = await asyncio.start_unix_server(
        lambda reader, writer: klippy_client(reader, writer, home), path=str(socket_path))
    log = home / "startup.log"
    env = dict(os.environ, HOME=str(home), PYTHONDONTWRITEBYTECODE="1")
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen([sys.executable, "-B", "-m", "moonraker",
                                    "-d", str(home / "printer_data")],
                                   cwd=installed, env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline, consecutive = time.monotonic() + 45, 0
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError(f"Moonraker exited with code {process.returncode}")
                try:
                    info = (await asyncio.to_thread(get_json, "/server/info"))["result"]
                except (OSError, ValueError):
                    await asyncio.sleep(0.25)
                    continue
                failed = required.intersection(info["failed_components"])
                if scenario == "invalid" and info["klippy_state"] == "ready":
                    assert "treed_detection" in failed, "Invalid config was not rejected"
                    assert not await asyncio.to_thread(core.ready, required), "Readiness accepted a failed component"
                    assert "No option 'frames_root'" in log.read_text(encoding="utf-8")
                    break
                if scenario != "invalid":
                    assert not failed, f"Required components failed: {sorted(failed)}"
                    ready = await asyncio.to_thread(core.ready, required)
                    consecutive = consecutive + 1 if ready else 0
                    if consecutive == 3:
                        for endpoint in ("/server/treed/detection/status", "/server/treed/autoprinter/status",
                                         "/server/treed/recovery/status"):
                            assert isinstance(await asyncio.to_thread(get_json, endpoint), dict)
                        break
                await asyncio.sleep(1)
            else:
                raise AssertionError("Delivered runtime did not reach stable readiness")
        except BaseException:
            print(log.read_text(encoding="utf-8", errors="replace")[-16000:], file=sys.stderr)
            raise
        finally:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, timeout=5)
            server.close()
            await server.wait_closed()
    print(f"MOONRAKER_STARTUP_OK: {scenario}, {len(required)} required components", flush=True)


# Блок 3: Без точного upstream SHA и runtime-пакета эта проверка не может быть зелёной.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--moonraker-source", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    args = parser.parse_args()
    manifest, payload = core.load_package(args.archive, "v" + (ROOT / "VERSION").read_text().strip())
    actual = core.git_head(args.moonraker_source)
    assert actual == manifest["requires"]["moonraker"], "Moonraker checkout does not match runtime pin"
    with tempfile.TemporaryDirectory(prefix="treed-startup-") as temporary:
        directory = Path(temporary)
        installed = directory / "installed"
        shutil.copytree(args.moonraker_source, installed,
                        ignore=shutil.ignore_patterns(".git", "__pycache__"))
        for scenario in ("packaged", "legacy", "invalid"):
            asyncio.run(run_scenario(directory, installed, payload, scenario))
    print("DEPLOYED_RUNTIME_STARTUP_PASSED")


if __name__ == "__main__":
    main()
