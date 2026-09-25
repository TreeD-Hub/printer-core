"""OFFLINE: acceptance orchestration, численные метрики и mesh persistence.

Контур: mocks и временные локальные пакеты; реальных движений и HTTP нет.
"""
import copy
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

import test_z_recovery as recovery

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('z_acceptance', ROOT/'tools/z_acceptance.py')
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


# Блок 1: Аппаратная граница диагностического extra без Klipper process.
class Command:
    error = ValueError
    def __init__(self, **params):
        self.params = dict(CONFIRM=1, **params)
    def get_command_parameters(self):
        return self.params
    def get_int(self, name, default=None):
        return self.params.get(name, default)
    def get_float(self, name, default=None):
        return self.params.get(name, default)


def mesh(offset=0.):
    return dict(probed_matrix=[[offset, offset+.01], [offset+.02, offset+.03]],
                mesh_matrix=[[offset, offset+.01], [offset+.02, offset+.03]],
                mesh_min=[10., 10.], mesh_max=[235., 210.], profile_name='treed_acceptance')


class MeshRig:
    def __init__(self, failure=None):
        self.rig = recovery.Rig()
        self.extra = self.rig.extra
        self.rig.homed = 'xyz'
        self.rig.objects['gcode_macro _TREED_OPERATION_STATE'].variables['phase'] = 'idle'
        self.pending = dict(save_config_pending=True, save_config_pending_items={'probe eddy': {'calibrate': 'existing'}})
        self.saved_pending = copy.deepcopy(self.pending)
        self.config_set = Mock(side_effect=lambda section, key, value:
                               self.pending['save_config_pending_items'].update({section: {key: value}}))
        self.original = Mock(side_effect=self.persist)
        self.bedmesh = NS(save_profile=self.original, get_status=lambda _: copy.deepcopy(self.mesh))
        self.bedmesh.get_mesh = lambda: NS(get_mesh_params=lambda: {'algo': 'bicubic'},
                                           get_z_range=lambda: (0., .03))
        self.mesh = {}
        self.calls = 0
        self.failure = failure
        self.during = None
        self.handlers = {'BED_MESH_CALIBRATE': self.scan, 'BED_MESH_CALIBRATE_BASE': self.scan}
        self.original_handlers = dict(self.handlers)
        self.rig.objects.update(
            configfile=NS(get_status=lambda _: self.pending, set=self.config_set), bed_mesh=self.bedmesh,
            gcode=NS(ready_gcode_handlers=self.handlers, register_command=self.register,
                     run_script_from_command=self.script))

    def register(self, name, handler):
        if handler is None:
            return self.handlers.pop(name, None)
        self.handlers[name] = handler

    def persist(self, name):
        self.config_set('bed_mesh '+name, 'points', 'new')

    def script(self, text):
        assert 'ACCEPTANCE=1' in text
        self.extra.cmd_mesh_run(Command())

    def scan(self, gcmd):
        self.calls += 1
        if self.during:
            self.during()
        if self.failure:
            raise self.failure
        self.mesh = mesh()
        self.bedmesh.save_profile('treed_acceptance')


class MeshPersistenceTests(unittest.TestCase):
    def check_restored(self, rig):
        self.assertIs(rig.bedmesh.save_profile, rig.original)
        for name, handler in rig.original_handlers.items():
            self.assertIs(rig.handlers[name], handler)
        self.assertFalse(rig.extra.mesh_active)
        self.assertEqual(rig.pending, rig.saved_pending)
        self.assertEqual(rig.extra.last_mesh['save_profile_restored'], 1)
        self.assertEqual(rig.extra.last_mesh['pending_config_changed'], 0)

    def test_success_preserves_pending_and_runtime_matrix(self):
        rig = MeshRig()
        rig.extra.cmd_mesh_test(Command())
        self.check_restored(rig)
        rig.original.assert_not_called()
        rig.config_set.assert_not_called()
        self.assertEqual(rig.calls, 1)
        self.assertEqual(rig.extra.last_mesh['mesh']['probed_matrix'], mesh()['probed_matrix'])
        self.assertEqual(rig.extra.last_mesh['mesh']['mesh_params'], {'algo': 'bicubic'})
        self.assertEqual(rig.extra.last_mesh['result'], 'passed')
        # Обычный production scan после диагностики снова сохраняет профиль.
        rig.handlers['BED_MESH_CALIBRATE'](Command())
        rig.original.assert_called_once_with('treed_acceptance')
        rig.config_set.assert_called_once_with('bed_mesh treed_acceptance', 'points', 'new')
        self.assertIn('bed_mesh treed_acceptance', rig.pending['save_config_pending_items'])

    def test_probe_timeout_gcode_and_connection_errors(self):
        for error in (ValueError('probe error'), TimeoutError('scan timeout'),
                      RuntimeError('G-code error'), ConnectionError('disconnected')):
            with self.subTest(error=error):
                rig = MeshRig(error)
                with self.assertRaises(type(error)):
                    rig.extra.cmd_mesh_test(Command())
                self.check_restored(rig)
                self.assertEqual(rig.extra.last_mesh['result'], 'failed')

    def test_exception_collecting_matrix(self):
        rig = MeshRig()
        rig.bedmesh.get_status = Mock(side_effect=ValueError('read matrix failed'))
        with self.assertRaises(ValueError):
            rig.extra.cmd_mesh_test(Command())
        self.assertTrue(rig.mesh)
        self.check_restored(rig)

    def test_reentrant_and_production_attempts_rejected(self):
        rig = MeshRig()
        def during():
            suppressed = rig.bedmesh.save_profile
            for command in (lambda: rig.extra.cmd_mesh_test(Command()),
                            lambda: rig.extra.cmd_mesh_run(Command()),
                            lambda: rig.handlers['BED_MESH_CALIBRATE'](Command()),
                            lambda: rig.handlers['BED_MESH_CALIBRATE_BASE'](Command())):
                with self.assertRaises(ValueError):
                    command()
                self.assertIs(rig.bedmesh.save_profile, suppressed)
        rig.during = during
        rig.extra.cmd_mesh_test(Command())
        self.assertEqual(rig.calls, 1)
        self.check_restored(rig)

    def test_pending_change_is_fault_without_cleanup(self):
        rig = MeshRig()
        rig.during = lambda: rig.pending['save_config_pending_items'].update({'foreign': {'x': '1'}})
        with self.assertRaisesRegex(ValueError, 'pending config changed'):
            rig.extra.cmd_mesh_test(Command())
        self.assertIs(rig.bedmesh.save_profile, rig.original)
        self.assertIn('foreign', rig.pending['save_config_pending_items'])
        self.assertEqual(rig.extra.last_mesh['result'], 'fault')
        self.assertTrue(rig.extra.mesh_fault)
        self.assertTrue(rig.rig.shutdown)

    def test_restore_failure_blocks_further_diagnostics(self):
        rig = MeshRig()
        class BrokenRestore:
            def __init__(self):
                self.handler = rig.original
            @property
            def save_profile(self):
                return self.handler
            @save_profile.setter
            def save_profile(self, value):
                if value is rig.original:
                    raise RuntimeError('restore failed')
                self.handler = value
            def get_status(self, now):
                return rig.mesh
            def get_mesh(self):
                return NS(get_mesh_params=lambda: {}, get_z_range=lambda: (0., .03))
        rig.bedmesh = BrokenRestore()
        rig.rig.objects['bed_mesh'] = rig.bedmesh
        with self.assertRaises(ValueError):
            rig.extra.cmd_mesh_test(Command())
        self.assertEqual(rig.extra.last_mesh['result'], 'fault')
        self.assertTrue(rig.extra.mesh_fault)
        with self.assertRaises(ValueError):
            rig.extra.cmd_mesh_test(Command())
        self.assertEqual(rig.calls, 1)


# Блок 2: Moonraker модель проверяет порядок, fail-fast и сырые данные.
class Client:
    def __init__(self, fail=None):
        self.sent = []
        self.fail = fail
        self.data = dict(webhooks=dict(state='ready'), print_stats=dict(state='standby'),
                         pause_resume=dict(is_paused=False), toolhead=dict(homed_axes=''),
                         configfile=dict(settings={'same': 1}), bed_mesh={},
                         treed_z_recovery=dict(last_run={}, last_eddy={}, last_mesh={}))
        self.data['gcode_macro _TREED_OPERATION_STATE'] = dict(phase='idle')

    def state(self):
        return copy.deepcopy(self.data)

    def command(self, script):
        self.sent.append(script)
        if self.fail is not None and len(self.sent) == self.fail:
            raise TimeoutError('reply lost; execution unknown')
        count = len(self.sent)
        if script.startswith('TREED_Z_RECOVERY_TEST'):
            probe = dict(trigger_mm=254.5, halt_mm=254.525, start_mm=249.5, overshoot_mm=.025)
            self.data['treed_z_recovery']['last_run'] = dict(timestamp=str(count), result='passed',
                probes=[dict(probe), dict(probe)], second_travel_mm=5., expected_second_travel_mm=5.,
                tolerance_mm=.5, deviation_mm=0., tmc_restored=True, tmc_before={'sgt': 3}, tmc_after={'sgt': 3})
            self.data['toolhead']['homed_axes'] = 'z'
        elif script.startswith('TREED_EDDY_ACCEPTANCE_HOME'):
            self.data['treed_z_recovery']['last_eddy'] = dict(timestamp=str(count), result='passed',
                stages={k: 'passed' for k in acceptance.STAGES[3:]}, z0_mcu_mm=12.+count*.001, motor_epoch=1)
        elif script.startswith('TREED_EDDY_ACCEPTANCE_MESH'):
            self.data['bed_mesh'] = mesh(count*.001)
            self.data['treed_z_recovery']['last_mesh'] = dict(timestamp=str(count), result='passed',
                mesh=self.data['bed_mesh'], save_profile_restored=1, pending_config_changed=0,
                mesh_profile_persistence_suppressed=1)
        else:
            self.data['toolhead']['homed_axes'] = 'xyz'


class RunnerTests(unittest.TestCase):
    def test_bootstrap_stage_order(self):
        with tempfile.TemporaryDirectory() as folder:
            client = Client()
            run = acceptance.Run(folder, 'bootstrap', client)
            self.assertTrue(run.execute(10, []))
            self.assertEqual(client.sent, ['TREED_Z_RECOVERY_TEST CONFIRM=1', 'G28 X', 'G28 Y',
                                           'TREED_EDDY_ACCEPTANCE_HOME CONFIRM=1'])
            self.assertEqual(set(run.result['stages'].values()), {'passed'})
            self.assertFalse(run.result['hardware_accepted'])

    def test_stage_failure_never_retries(self):
        for stage in range(1, 5):
            with tempfile.TemporaryDirectory() as folder:
                client = Client(stage)
                run = acceptance.Run(folder, 'bootstrap', client)
                self.assertFalse(run.execute(10, []))
                self.assertEqual(len(client.sent), stage)
                self.assertEqual(acceptance.read(Path(folder)/'result.json')['result'], 'failed')

    def test_multiple_starts_and_numeric_results(self):
        with tempfile.TemporaryDirectory() as folder:
            client = Client()
            run = acceptance.Run(folder, 'bottom', client)
            self.assertTrue(run.execute(10, [20., 100., 250.]))
            self.assertEqual(len(client.sent), 10)
            self.assertIn('START_Z=100.000000', client.sent[1])
            self.assertEqual(run.result['z_bottom']['second_travel']['median'], 5.)
            self.assertEqual(run.result['z_bottom']['overshoot']['max'], .025)

    def test_z0_and_mesh_series(self):
        for mode in ('z0', 'mesh'):
            with tempfile.TemporaryDirectory() as folder:
                run = acceptance.Run(folder, mode, Client())
                self.assertTrue(run.execute(3, []))
                if mode == 'z0':
                    self.assertAlmostEqual(run.result['eddy_z0']['range_mm'], .002)
                else:
                    self.assertEqual(len(run.result['mesh']['raw']), 3)
                    self.assertAlmostEqual(run.result['mesh']['max_abs_delta_mm'], .002)

    def test_busy_printer_has_no_motion(self):
        with tempfile.TemporaryDirectory() as folder:
            client = Client()
            client.data['print_stats']['state'] = 'printing'
            run = acceptance.Run(folder, 'bottom', client)
            self.assertFalse(run.execute(10, []))
            self.assertEqual(client.sent, [])


# Блок 3: Независимые численные controls и evidence, не означающие hardware PASS.
class DiagnosticEntryTests(unittest.TestCase):
    def rig(self):
        rig = recovery.Rig()
        rig.homed = 'xyz'
        rig.objects['gcode_macro _TREED_OPERATION_STATE'].variables['phase'] = 'idle'
        return rig

    def test_start_move_precedes_explicit_loss_of_z(self):
        rig = self.rig()
        rig.objects['gcode'].create_gcode_command = lambda *args: rig.command
        def recovered(command):
            self.assertNotIn('z', rig.homed)
            self.assertEqual(rig.pos[2], 100.)
        rig.extra.cmd_home = Mock(side_effect=recovered)
        rig.extra.cmd_test(Command(START_Z=100.))
        self.assertEqual(rig.extra.last_run['before_forced_unknown']['homed_axes'], 'xyz')
        self.assertEqual(len(rig.moves), 1)

    def test_invalid_start_has_no_motion_or_coordinate_loss(self):
        for start in (float('nan'), -1., 251.):
            rig = self.rig()
            with self.assertRaises(ValueError):
                rig.extra.cmd_test(Command(START_Z=start))
            self.assertEqual(rig.moves, [])
            self.assertEqual(rig.homed, 'xyz')

    def test_z0_uses_mcu_reference_and_failure_retains_stage(self):
        rig = self.rig()
        extra = rig.extra
        extra.capture_eddy = True
        extra.last_eddy = {'stages': {'final_z0': 'running'}}
        stepper = NS(get_mcu_position=lambda: 10000, get_step_dist=lambda: .001)
        rig.kin.rails[2].get_steppers = lambda: [stepper]
        rig.pos[2] = .25
        command = NS(get=lambda name: {'STAGE': 'final_z0', 'STATE': 'passed'}[name], error=ValueError)
        extra.cmd_stage(command)
        self.assertEqual(extra.last_eddy['z0_mcu_mm'], 9.75)
        extra.last_eddy['stages']['final_z0'] = 'running'
        stepper.get_mcu_position = Mock(side_effect=ValueError('counter error'))
        with self.assertRaises(ValueError):
            extra.cmd_stage(command)
        self.assertEqual(extra.last_eddy['stages']['final_z0'], 'running')


class EvidenceTests(unittest.TestCase):
    def package(self, folder):
        path = Path(folder)
        for name in ('moonraker', 'versions', 'can', 'kernel', 'klipper'):
            (path/name).mkdir()
        (path/'manifest.env').write_text('mode=acceptance\nrun_id=test0001\nsave_config_sent=0\n')
        (path/'versions/printer-core.txt').write_text('commit='+'a'*40+'\n')
        (path/'versions/boot.txt').write_text('boot_id=boot1\n')
        for name in ('kernel/messages.interval.txt', 'klipper/klippy.interval.log',
                     'klipper/session-boundaries.txt'):
            (path/name).write_text('captured\n')
        acceptance.save(path/'versions/mcu-live.json', {'result': {'status': {}}})
        state = dict(configfile={'save_config_pending_items': {'existing': {'v': 1}}},
                     webhooks={'state': 'ready'})
        for name in ('mcu', 'mcu EBBCan', 'mcu eddy'):
            state[name] = {'last_stats': {'bytes_retransmit': 2, 'bytes_invalid': 0}}
        for side in ('before', 'after'):
            acceptance.save(path/('moonraker/acceptance-'+side+'.json'), {'result': {'status': state}})
            acceptance.save(path/('moonraker/printer-info-'+side+'.json'), {'result': {'process_id': 123}})
            acceptance.save(path/('can/counters.'+side+'.json'), [{'stats64': {'rx': {'errors': 0}, 'tx': {'errors': 0}}}])
        acceptance.save(path/'result.json', dict(schema_version=1, mode='cold-start', result='measured_pass',
            initial_state=state, z_bottom={'raw': [{'second_travel_mm': 5.}]},
            mesh={'raw': [mesh()]}, mesh_diagnostics={'raw': [dict(result='passed',
                save_profile_restored=1, mesh_profile_persistence_suppressed=1, pending_config_changed=0)]}))
        return path

    def test_complete_evidence_and_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.package(folder)
            self.assertTrue(acceptance.finalize(path))
            result = acceptance.read(path/'result.json')
            self.assertEqual(result['evidence_status'], 'complete')
            self.assertFalse(result['hardware_accepted'])
            for flag in ('save_profile_restored=1', 'pending_config_changed=0',
                         'mesh_profile_persistence_suppressed=1', 'save_config_sent=0'):
                self.assertIn(flag, (path/'manifest.env').read_text())

    def test_evidence_rejects_pending_changes_missing_suppression_and_restart(self):
        for defect in ('pending', 'suppression', 'restart', 'communication', 'restore_fault'):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as folder:
                path = self.package(folder)
                if defect in ('suppression', 'restore_fault'):
                    result = acceptance.read(path/'result.json')
                    sample = result['mesh_diagnostics']['raw'][0]
                    if defect == 'suppression':
                        sample['mesh_profile_persistence_suppressed'] = 0
                    else:
                        sample.update(result='fault', save_profile_restored=0)
                    acceptance.save(path/'result.json', result)
                elif defect == 'restart':
                    acceptance.save(path/'moonraker/printer-info-after.json', {'result': {'process_id': 456}})
                else:
                    snapshot = acceptance.read(path/'moonraker/acceptance-after.json')
                    state = snapshot['result']['status']
                    if defect == 'pending':
                        state['configfile']['save_config_pending_items']['bed_mesh eddy_diag_test'] = {'points': 'new'}
                    else:
                        state['mcu']['last_stats']['bytes_invalid'] = 1
                    acceptance.save(path/'moonraker/acceptance-after.json', snapshot)
                self.assertFalse(acceptance.finalize(path))
                result = acceptance.read(path/'result.json')
                self.assertNotEqual(result['result'], 'measured_pass')
                if defect == 'restore_fault':
                    self.assertEqual(result['result'], 'fault')
                self.assertFalse(result['hardware_accepted'])

    def test_cold_start_comparison_requires_independent_boots(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            paths = [self.package(one), self.package(two)]
            for path in paths:
                self.assertTrue(acceptance.finalize(path))
            with self.assertRaisesRegex(ValueError, 'одной загрузке'):
                acceptance.compare(paths)
            result = acceptance.read(paths[1]/'result.json')
            result['boot_id'] = 'boot2'
            acceptance.save(paths[1]/'result.json', result)
            compared = acceptance.compare(paths)
            self.assertEqual(compared['mesh']['max_abs_delta_mm'], 0)
            self.assertFalse(compared['hardware_accepted'])


class MetricsTests(unittest.TestCase):
    def test_statistics(self):
        result = acceptance.stats([1, 2, 3])
        self.assertEqual(result['median'], 2)
        self.assertEqual(result['range'], 2)
        self.assertEqual(result['standard_deviation'], 1)
        compared = acceptance.mesh_stats([mesh(), mesh(.1), mesh(.2)])
        self.assertAlmostEqual(compared['median_abs_delta_mm'], .1)
        self.assertAlmostEqual(compared['p95_abs_delta_mm'], .2)
        self.assertAlmostEqual(compared['rms_delta_mm'], math.sqrt(.02))

    def test_mismatched_mesh_and_missing_counters(self):
        other = mesh()
        other['mesh_min'] = [11, 10]
        with self.assertRaises(ValueError):
            acceptance.mesh_stats([mesh(), other])
        self.assertIsNone(acceptance.counter_delta(5, 4))
        self.assertIsNone(acceptance.counter_delta(None, 4))
        self.assertEqual(acceptance.counter_delta(5, 7), 2)
        for values in ([], [float('nan')], [float('inf')]):
            with self.assertRaises(ValueError):
                acceptance.stats(values)


if __name__ == '__main__':
    result = unittest.main(exit=False)
    if not result.result.wasSuccessful():
        sys.exit(1)
    print('Z_ACCEPTANCE_OFFLINE_PASS')
