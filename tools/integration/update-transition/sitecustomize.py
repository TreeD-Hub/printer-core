"""Подмена только транспорта тестового GitHub release; проверки updater не меняются."""
from io import BytesIO
from pathlib import Path
import re
import urllib.request

# Блок 1: Разрешены только фиксированные адреса тестового release.
original = urllib.request.urlopen
root = Path('/opt/treed-fixture/releases')

# Блок 2: Реальный updater проверяет адрес, хеши и envelope полученных байтов.
def open_fixture(request, *args, **kwargs):
    url = request.full_url if hasattr(request, 'full_url') else str(request)
    api = re.fullmatch(r'https://api\.github\.com/repos/TreeD-Hub/printer-core/releases/tags/(v[0-9]+\.[0-9]+\.[0-9]+)', url)
    asset = re.fullmatch(r'https://github\.com/TreeD-Hub/printer-core/releases/download/(v[0-9]+\.[0-9]+\.[0-9]+)/(treed-core-runtime\.zip|treed-mainshellos-source\.zip)', url)
    if api:
        path = root / api.group(1) / 'release.json'
    elif asset:
        path = root / asset.group(1) / asset.group(2)
    else:
        return original(request, *args, **kwargs)
    with open('/var/log/treed-fixture-transport.log', 'a', encoding='utf-8') as log:
        log.write(url + '\n')
    return BytesIO(path.read_bytes())

urllib.request.urlopen = open_fixture
