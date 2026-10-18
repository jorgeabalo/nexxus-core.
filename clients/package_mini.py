#!/usr/bin/env python3
"""Package only allowlisted client files; never copy repo credentials or databases."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import tempfile
import zipfile

SOURCE = Path(__file__).resolve().parent / 'nexxus-mini'
FILES = ('client.py', 'diagnose.py', 'Nexxus.command', 'Diagnostico.command',
         'record-audio.c', 'config.example.json', 'INSTALACION.md')
BINARIES = ('transport/curl', 'transport/cacert.pem', 'record-audio')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package(source, output, evidence=None):
    source, output = Path(source), Path(output)
    if output.exists():
        raise ValueError('El archivo de salida ya existe; elige un nombre nuevo.')
    ready = evidence is not None
    if ready:
        report = json.loads(Path(evidence).read_text())
        if report.get('os') != '10.6.8' or report.get('transport_verified_on_target') is not True:
            raise ValueError('Falta verificar HTTPS en Snow Leopard 10.6.8.')
        for name in BINARIES:
            path = source / name
            if not path.is_file() or path.is_symlink() or digest(path) != report.get('sha256', {}).get(name):
                raise ValueError('El binario o su hash no coincide con el diagnostico: ' + name)
        if not (source / 'transport/NOTICE.txt').is_file():
            raise ValueError('Faltan licencias y procedencia del transporte en transport/NOTICE.txt.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nexxus-package-') as tmp:
        root = Path(tmp) / 'NexxusMiniAI'
        contents = root / 'Nexxus Mini AI.app/Contents'
        resources = contents / 'Resources/client'
        resources.mkdir(parents=True)
        for name in FILES + (BINARIES + ('transport/NOTICE.txt',) if ready else ()):
            src = source / name
            if src.is_symlink() or not src.is_file():
                raise ValueError('Archivo necesario ausente o enlace no permitido: ' + name)
            dest = resources / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            dest.chmod(0o755 if name.endswith('.command') or name in ('record-audio', 'transport/curl') else 0o644)
        # Deliberately do not package config.json: installer configuration is separate.
        launcher = contents / 'MacOS/Nexxus'
        launcher.parent.mkdir()
        launcher.write_text('#!/bin/sh\ncd "$(dirname "$0")/../Resources/client" || exit 1\nexec /usr/bin/python client.py\n')
        launcher.chmod(0o755)
        with (contents / 'Info.plist').open('wb') as handle:
            plistlib.dump({'CFBundleName': 'Nexxus Mini AI', 'CFBundleDisplayName': 'Nexxus Mini AI',
                          'CFBundleIdentifier': 'com.aita.nexxus-mini', 'CFBundleExecutable': 'Nexxus',
                          'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': '0.2.0',
                          'CFBundleVersion': '2', 'LSMinimumSystemVersion': '10.6'}, handle)
        note = ('PAQUETE PARA PRUEBAS: transporte verificado en 10.6.8; falta validar la conversacion completa.'
                if ready else 'PAQUETE DE DESARROLLO: falta transporte HTTPS y capturador compilados para 10.6.8. NO listo para conversar.')
        (root / 'LEEME.txt').write_text(note + '\n\nLa instalacion esta en Nexxus Mini AI.app/Contents/Resources/client/INSTALACION.md\n', encoding='utf-8')
        manifest = {str(p.relative_to(root)): digest(p) for p in sorted(root.rglob('*')) if p.is_file()}
        (root / 'SHA256.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob('*')):
                archive.write(path, str(path.relative_to(root.parent)))
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--evidence', help='JSON de diagnose.py --online ejecutado en 10.6.8')
    args = parser.parse_args()
    try:
        print(package(SOURCE, args.output, args.evidence))
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')
