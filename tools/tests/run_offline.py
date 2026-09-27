"""Полный offline regression набор; временные fixtures, без подключения к принтеру.

Контур: запускает только tests/test_*; ошибка или недоступный интерпретатор
дают ненулевой exit code. Аппаратные gates не изменяет.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys


# Блок 1: Явные интерпретаторы и последовательный запуск имеющихся проверок.
def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    root = Path(__file__).resolve().parents[2]
    tests = root / 'tools/tests'
    pwsh = shutil.which('pwsh')
    bash = shutil.which('bash')
    if os.name == 'nt':
        git = shutil.which('git')
        git_bash = Path(git).parent.parent / 'bin/bash.exe' if git else None
        bash = str(git_bash) if git_bash and git_bash.is_file() else None
    runners = {'.py': [sys.executable, '-B'], '.ps1': [pwsh, '-NoProfile', '-File'], '.sh': [bash]}
    failed, count = [], 0
    for path in sorted(tests.glob('test_*')):
        if path.suffix not in runners:
            continue
        count += 1
        command = runners[path.suffix]
        if command[0] is None:
            failed.append(path.name+': interpreter unavailable')
            continue
        script = str(path)
        if os.name == 'nt' and path.suffix == '.sh':
            script = '/' + path.drive[0].lower() + path.as_posix()[2:]
        try:
            result = subprocess.run(command+[script], cwd=root, capture_output=True,
                                    encoding='utf-8', errors='replace', timeout=180)
            if result.returncode:
                failed.append(path.name)
                print('FAIL '+path.name+'\n'+(result.stdout+result.stderr)[-5000:], flush=True)
            else:
                print('PASS '+path.name, flush=True)
        except (OSError, subprocess.TimeoutExpired) as exc:
            failed.append(path.name+': '+str(exc))
    if failed:
        print('OFFLINE_REGRESSION_FAILED: '+ '; '.join(failed))
        return 1
    print('OFFLINE_REGRESSION_PASS: %d scripts' % count)
    return 0


if __name__ == '__main__':
    sys.exit(main())
