#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Offline-first USB checks, compatible with Python 2.6. Never reads tokens."""
from __future__ import unicode_literals
import hashlib
import json
import os
import platform
import subprocess
import sys
ROOT = os.path.dirname(os.path.abspath(__file__))


def verify_manifest(root=ROOT):
    with open(os.path.join(root, 'MANIFEST.json'), 'rb') as handle:
        manifest = json.loads(handle.read().decode('utf-8'))
    for item in manifest['files']:
        path = item['path']
        if path.startswith('/') or '..' in path.split('/'):
            raise ValueError('Ruta de manifiesto no valida')
        with open(os.path.join(root, path), 'rb') as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        if digest != item['sha256']:
            raise ValueError('Archivo incompleto o modificado: ' + path)
    return len(manifest['files'])


def main():
    print('Nexxus Mini AI - comprobacion del paquete USB')
    try:
        print('Integridad: %s archivos correctos' % verify_manifest())
        if platform.system() != 'Darwin':
            raise ValueError('Este paquete requiere Mac OS X Intel.')
        print('Sistema: Mac OS X %s; CPU: %s' % (platform.mac_ver()[0], platform.machine()))
        if platform.machine() not in ('i386', 'x86_64'):
            raise ValueError('El transporte incluido es Intel; no se ejecuta en Apple Silicon.')
        proc = subprocess.Popen([os.path.join(ROOT, 'transport', 'curl'), '-q', '--version'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output, unused = proc.communicate()
        if proc.returncode:
            raise ValueError('El transporte no pudo iniciar. Conservar este resultado para revision.')
        print(output.decode('utf-8', 'replace'))
        first = output.decode('ascii', 'replace').splitlines()[0]
        version = tuple(int(x) for x in first.split()[1].split('.'))
        if version < (8, 4, 0) or 'OpenSSL/' not in first:
            raise ValueError('Se necesita curl moderno con OpenSSL.')
        try:
            import Tkinter
        except ImportError:
            import tkinter
        print('Tk: disponible')
        if not os.path.isfile(os.path.join(ROOT, 'system-metrics')):
            raise ValueError('Falta el lector de metricas Intel.')
        print('Metricas: lector local incluido; sin acceso al microfono.')
        print('Comprobacion local completada. No se enviaron datos ni se probo el servidor.')
        return 0
    except Exception as exc:
        print('PENDIENTE: ' + str(exc))
        return 1


if __name__ == '__main__':
    sys.exit(main())
