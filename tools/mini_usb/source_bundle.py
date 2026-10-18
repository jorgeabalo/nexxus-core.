"""Include exact source archives, notices and signed package build metadata."""
import hashlib
import json
import shutil
from pathlib import Path
import tarfile
from urllib.parse import unquote
from fetch import download


def sources(root, definitions):
    output = root / 'Nexxus-Mini-AI' / 'SOURCES'
    output.mkdir(exist_ok=True)
    archives = json.loads((definitions / 'ports-lock.json').read_text())
    records = json.loads((definitions / 'sources-lock.json').read_text())
    for record in records:
        name = record['name']
        target = output / name
        target.mkdir(exist_ok=True)
        package = next(r for r in archives if r['name'] == name)
        package_path = root / 'archives' / unquote(package['url'].rsplit('/', 1)[-1])
        with tarfile.open(package_path) as archive:
            for special in ['+PORTFILE', '+CONTENTS', '+STATE']:
                member = next(m for m in archive if m.name.removeprefix('./') == special)
                (target / special[1:]).write_bytes(archive.extractfile(member).read())
        if 'url' not in record:  # CA data is already included in transport/cacert.pem
            continue
        path = target / unquote(record['url'].rsplit('/', 1)[-1])
        cached = root / 'archives' / path.name
        download(record['url'], cached, record['sha256'])
        shutil.copyfile(cached, path)
        if record['sha256'] not in (target / 'PORTFILE').read_text():
            raise ValueError('Source checksum not in signed package metadata')
        with tarfile.open(path) as archive:
            for member in archive:
                if (member.isfile() and Path(member.name).name.upper().startswith(
                        ('LICENSE', 'COPYING', 'COPYRIGHT', 'NOTICE'))
                        and '..' not in Path(member.name).parts and not member.name.startswith('/')):
                    dest = target / 'licenses' / member.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(archive.extractfile(member).read())
    (output / 'SOURCE-LOCK.json').write_text(json.dumps(records, indent=2) + '\n')

    for record in json.loads((definitions / 'build-files-lock.json').read_text()):
        relative = Path(record['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe build metadata path')
        cached = root / 'build-files' / relative
        cached.parent.mkdir(parents=True, exist_ok=True)
        download(record['url'], cached, record['sha256'])
        target = output / 'MacPorts-build-files' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cached, target)
