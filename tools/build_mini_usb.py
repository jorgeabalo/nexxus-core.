#!/usr/bin/env python3
"""Build an evaluation USB zip on a modern Mac; never deploys or installs system files."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
DEFINITIONS = PROJECT / 'tools' / 'mini_usb'
sys.path.insert(0, str(DEFINITIONS))
from fetch import fetch
from source_bundle import sources


def finish(root, output, server):
    package = root / 'Nexxus-Mini-AI'
    (package / 'config.json').write_text(json.dumps({'server': server}, indent=2) + '\n')
    shutil.copy2(DEFINITIONS / 'macports-pubkey.pem', package / 'MACPORTS-PUBLIC-KEY.pem')
    shutil.copy2(DEFINITIONS / 'LEEME.txt', package / 'LEEME.txt')
    shutil.copytree(DEFINITIONS, package / 'SOURCES' / 'packaging',
                    ignore=shutil.ignore_patterns('__pycache__'), dirs_exist_ok=True)
    shutil.copy2(__file__, package / 'SOURCES' / 'build_mini_usb.py')
    manifest = []
    for path in sorted(package.rglob('*')):
        if path.is_file() and path.name != 'MANIFEST.json' and path != package / 'config.json':
            manifest.append({'path': str(path.relative_to(package)),
                             'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    (package / 'MANIFEST.json').write_text(json.dumps({'files': manifest}, indent=2) + '\n')
    output.mkdir(parents=True, exist_ok=True)
    archive = output / 'Nexxus-Mini-AI-USB-evaluacion.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(package.rglob('*')):
            if path.is_file():
                bundle.write(path, str(path.relative_to(root)))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / (archive.name + '.sha256')).write_text(digest + '  ' + archive.name + '\n')
    print(str(archive))
    print('SHA256: ' + digest)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path, required=True, help='Fresh work directory')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--server', required=True, help='Approved HTTPS origin, no path')
    parser.add_argument('--finish-only', action='store_true', help='Reuse already audited staging files')
    args = parser.parse_args()
    url = urlsplit(args.server)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password or
            url.path not in ('', '/') or url.query or url.fragment):
        parser.error('server must be an HTTPS origin without credentials or path')
    root = args.work.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not args.finish_only:
        fetch(root, DEFINITIONS)
        subprocess.run(['clang', '-arch', 'x86_64', '-mmacosx-version-min=10.6',
                        '-Wall', '-Wextra', '-Werror', '-Wl,-no_fixup_chains',
                        '-framework', 'AudioToolbox', '-framework', 'CoreFoundation',
                        str(PROJECT / 'clients/nexxus-mini/record-audio.c'),
                        '-o', str(root / 'record-audio-intel')], check=True)
        subprocess.run([sys.executable, str(DEFINITIONS / 'assemble.py'), str(root)], check=True)
        sources(root, DEFINITIONS)
    finish(root, args.output.resolve(), args.server)


if __name__ == '__main__':
    main()
