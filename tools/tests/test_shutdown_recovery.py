import asyncio
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile


REPO = pathlib.Path(__file__).resolve().parents[2]


def load_module(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, REPO / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


update = load_module("treed_update_test", "moonraker/components/treed_update.py")
recovery = load_module("treed_recovery_test", "moonraker/components/treed_recovery.py")


class FakeConfig:
    def __init__(self, server, values):
        self.server = server
        self.values = values

    def get_server(self):
        return self.server

    def get(self, key, default=None):
        return self.values.get(key, default)


class FirmwareKlippy:
    def __init__(self, info, objects):
        self.info = info
        self.objects = objects

    async def get_klippy_info(self, default=None):
        return self.info

    async def query_objects(self, objects, default=None):
        return self.objects


class FirmwareServer:
    def __init__(self, klippy):
        self.klippy = klippy
        self.endpoints = []

    def lookup_component(self, name):
        assert name == "klippy_apis"
        return self.klippy

    def register_endpoint(self, *args, **kwargs):
        self.endpoints.append((args, kwargs))


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def write_artifact(path, content):
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


async def test_firmware_status():
    with tempfile.TemporaryDirectory() as temp_name:
        root = pathlib.Path(temp_name)
        klipper = root / "klipper"
        klipper.mkdir()
        git(klipper, "init")
        git(klipper, "config", "user.email", "test@example.invalid")
        git(klipper, "config", "user.name", "TreeD test")
        (klipper / "revision.txt").write_text("old\n", encoding="utf-8")
        git(klipper, "add", "revision.txt")
        git(klipper, "commit", "-m", "old")
        old_commit = git(klipper, "rev-parse", "HEAD")
        (klipper / "revision.txt").write_text("current\n", encoding="utf-8")
        git(klipper, "commit", "-am", "current")
        expected = git(klipper, "rev-parse", "HEAD")

        runtime_manifest = root / "runtime-versions.env"
        runtime_manifest.write_text(
            f'TREED_KLIPPER_REF="{expected}"\n',
            encoding="utf-8",
        )
        rows = []
        for target in ("main_octopus", "ebb42_can", "eddy_can"):
            target_dir = root / target
            target_dir.mkdir()
            artifact = target_dir / "firmware.bin"
            config = target_dir / "target.config"
            dictionary = target_dir / "klipper.dict"
            artifact_sha = write_artifact(artifact, target.encode())
            config_sha = write_artifact(config, (target + "-config").encode())
            dictionary_sha = write_artifact(dictionary, (target + "-dict").encode())
            rows.append((
                target,
                artifact,
                artifact_sha,
                expected,
                config,
                config_sha,
                dictionary,
                dictionary_sha,
            ))
        firmware_manifest = root / "manifest.tsv"
        firmware_manifest.write_text(
            "target\tartifact\tartifact_sha256\tklipper_commit\tconfig\tconfig_sha256\tdictionary\tdictionary_sha256\n"
            + "".join("\t".join(map(str, row)) + "\n" for row in rows),
            encoding="utf-8",
        )

        objects = {
            "mcu": {"mcu_version": f"v0.13.0-1-g{expected[:12]}", "last_stats": {"bytes_read": 10}},
            "mcu EBBCan": {"mcu_version": f"v0.13.0-1-g{old_commit[:9]}", "last_stats": {"bytes_read": 20}},
            "mcu eddy": {"mcu_version": expected[:7], "last_stats": {"bytes_read": 30}},
        }
        klippy_api = FirmwareKlippy(
            {"state": "ready", "software_version": f"v0.13.0-1-g{expected[:12]}"},
            objects,
        )
        server = FirmwareServer(klippy_api)
        observation = root / "observed.json"
        component = update.TreeDUpdate(FakeConfig(server, {
            "repo_path": str(root),
            "runtime_manifest_path": str(runtime_manifest),
            "klipper_repo_path": str(klipper),
            "firmware_manifest_path": str(firmware_manifest),
            "firmware_observation_file": str(observation),
        }))
        status = await component._firmware_status()
        by_id = {item["id"]: item for item in status["mcus"]}
        assert status["status"] == "update_required"
        assert status["host"]["status"] == "current"
        assert status["buildStatus"] == "current"
        assert by_id["main_octopus"]["status"] == "current"
        assert by_id["ebb42_can"]["status"] == "update_required"
        assert by_id["eddy_can"]["status"] == "current"
        assert by_id["eddy_can"]["reportedCommit"] == expected
        assert "требуется обновление MCU" in status["message"]

        klippy_api.info = {"state": "shutdown", "state_message": "Lost communication with MCU 'EBBCan'"}
        klippy_api.objects = {}
        unavailable = await component._firmware_status()
        assert unavailable["status"] == "unreachable"
        for item in unavailable["mcus"]:
            assert item["status"] == "unreachable"
            assert item["lastKnown"]["stale"] is True


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    async def sleep(self, seconds):
        self.value += seconds


class RecoveryKlippy:
    def __init__(self, samples, info_samples=None):
        self.samples = list(samples)
        self.info_samples = list(info_samples or [
            {"state": "ready"},
            {"state": "ready"},
            {"state": "startup"},
            {"state": "ready"},
        ])
        self.restart_calls = 0
        self.sample_index = 0
        self.info_index = 0

    async def do_restart(self, command):
        assert command == "FIRMWARE_RESTART"
        self.restart_calls += 1

    async def get_klippy_info(self, default=None):
        info = self.info_samples[min(self.info_index, len(self.info_samples) - 1)]
        self.info_index += 1
        return info

    async def query_objects(self, objects, default=None):
        sample = self.samples[min(self.sample_index, len(self.samples) - 1)]
        self.sample_index += 1
        return sample


class RecoveryServer:
    def __init__(self, klippy):
        self.klippy = klippy
        self.endpoints = []
        self.event_handlers = {}

    def lookup_component(self, name):
        assert name == "klippy_apis"
        return self.klippy

    def get_event_loop(self):
        return asyncio.get_running_loop()

    def register_endpoint(self, *args, **kwargs):
        self.endpoints.append((args, kwargs))

    def register_event_handler(self, event, handler):
        self.event_handlers[event] = handler


def recovery_sample(main_read, ebb_read, eddy_read, *, state="ready", message=""):
    def mcu(version, value):
        return {
            "mcu_version": version,
            "last_stats": {
                "bytes_read": value,
                "bytes_retransmit": 0,
                "bytes_invalid": 0,
                "tx_retries": 0,
            },
        }

    return {
        "webhooks": {"state": state, "state_message": message},
        "mcu": mcu("main", main_read),
        "mcu EBBCan": mcu("ebb", ebb_read),
        "mcu eddy": mcu("eddy", eddy_read),
    }


async def run_recovery_case(root, samples, *, observe=5, stale=3, info_samples=None):
    klippy = RecoveryKlippy(samples, info_samples)
    component = recovery.TreeDRecovery(FakeConfig(RecoveryServer(klippy), {
        "state_file": str(root / "recovery.json"),
        "ready_timeout": 3,
        "observe_seconds": observe,
        "poll_interval": 1,
        "stale_seconds": stale,
    }))
    clock = FakeClock()
    component._monotonic = clock.monotonic
    component._sleep = clock.sleep
    await component._run_recovery("attempt")
    return component._read_state(), klippy


async def test_recovery():
    with tempfile.TemporaryDirectory() as temp_name:
        root = pathlib.Path(temp_name)
        stable_samples = [recovery_sample(i, i, i) for i in range(1, 8)]
        state, klippy = await run_recovery_case(root / "stable", stable_samples)
        assert state["status"] == "stable"
        assert klippy.restart_calls == 1

        stale_samples = [recovery_sample(i, 1, i) for i in range(1, 25)]
        state, _ = await run_recovery_case(
            root / "stale",
            stale_samples,
            observe=20,
            stale=13,
            info_samples=[
                {"state": "shutdown", "state_message": "Shutdown due to webhooks request"},
                {"state": "startup"},
                {"state": "ready"},
            ],
        )
        assert state["status"] == "failed"
        assert state["failedMcu"] == "EBBCan"
        assert "Stale data" in state["message"]
        assert state["counterDeltas"]["EBBCan"]["bytes_read"] == 0

        reset_samples = [
            recovery_sample(10, 10, 10),
            recovery_sample(11, 1, 11),
        ]
        state, _ = await run_recovery_case(root / "counter-reset", reset_samples)
        assert state["status"] == "failed"
        assert state["failedMcu"] == "EBBCan"
        assert "Counters reset" in state["message"]

        missing = recovery_sample(1, 1, 1)
        del missing["mcu EBBCan"]
        state, _ = await run_recovery_case(root / "missing", [missing])
        assert state["status"] == "failed"
        assert state["failedMcu"] == "EBBCan"
        assert state["reidentifiedMcus"] == ["Octopus", "Eddy"]

        emergency = recovery_sample(
            1,
            1,
            1,
            state="shutdown",
            message="Shutdown due to webhooks request",
        )
        state, _ = await run_recovery_case(root / "emergency", [emergency])
        assert state["status"] == "emergency_stop"
        assert state["phase"] == "cancelled_by_emergency_stop"

        state, _ = await run_recovery_case(
            root / "emergency-before-ready",
            stable_samples,
            info_samples=[
                {"state": "ready"},
                {"state": "startup"},
                {"state": "shutdown", "state_message": "Shutdown due to webhooks request"},
            ],
        )
        assert state["status"] == "emergency_stop"
        assert state["phase"] == "cancelled_by_emergency_stop"

        klippy = RecoveryKlippy(stable_samples)
        component = recovery.TreeDRecovery(FakeConfig(RecoveryServer(klippy), {
            "state_file": str(root / "single.json"),
            "ready_timeout": 3,
            "observe_seconds": 5,
            "poll_interval": 1,
            "stale_seconds": 3,
        }))
        clock = FakeClock()
        component._monotonic = clock.monotonic
        component._sleep = clock.sleep
        await component._handle_start(object())
        first_task = component._task
        await component._handle_start(object())
        assert component._task is first_task
        assert first_task is not None
        await first_task
        assert klippy.restart_calls == 1
        assert any(
            item.get("requestedAction") == "FIRMWARE_RESTART"
            for item in component._read_state()["history"]
        )


async def main():
    await test_firmware_status()
    await test_recovery()


asyncio.run(main())
print("PASS: shutdown recovery behavior")
