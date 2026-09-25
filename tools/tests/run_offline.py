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
    root = Path(__file__).resolve().parents[2]
    tests = root / 'tools/tests'
    pwsh = shutil.which('pwsh')
    bash = shutil.which('bash')
    if os.name == 'nt' and Path('C:/Program Files/Git/bin/bash.exe').exists():
        bash = 'C:/Program Files/Git/bin/bash.exe'
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
        try:
            result = subprocess.run(command+[str(path)], cwd=root, capture_output=True,
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
