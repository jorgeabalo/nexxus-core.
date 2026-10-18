#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Offline checks, plus optional unauthenticated HTTPS health check. Python 2.6."""
from __future__ import print_function, unicode_literals
import hashlib
import json
import os
import platform
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def checksum(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while True:
            block = handle.read(65536)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def diagnose(online=False):
    report = {'os': platform.mac_ver()[0], 'machine': platform.machine(),
              'python': platform.python_version(), 'checks': {}, 'sha256': {}}
    checks = report['checks']
    checks['snow_leopard'] = report['os'] == '10.6.8'
    try:
        if sys.version_info[0] == 2:
            import Tkinter
        else:
            import tkinter
        checks['tk_import'] = True
    except ImportError:
        checks['tk_import'] = False
    for name in ['transport/curl', 'transport/cacert.pem', 'record-audio']:
        path = os.path.join(ROOT, name)
        checks[name] = os.path.isfile(path)
        if checks[name]:
            report['sha256'][name] = checksum(path)
    checks['curl_tls12'] = False
    binary = os.path.join(ROOT, 'transport', 'curl')
    if checks['transport/curl']:
        try:
            proc = subprocess.Popen([binary, '-q', '--version'], stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
            out, unused = proc.communicate()
            lines = out.decode('ascii', 'replace').splitlines()
            version = lines[0].split()[1].split('.')
            # 8.4+ enforces max-filesize during chunked downloads.
            checks['curl_tls12'] = (proc.returncode == 0 and
                tuple(int(v) for v in version[:3]) >= (8, 4, 0) and
                any(line.startswith('Features:') and 'SSL' in line.split() for line in lines) and
                any(line.startswith('Protocols:') and 'https' in line.split() for line in lines))
        except (OSError, ValueError, IndexError):
            pass
    checks['https_health'] = False
    if online and checks['curl_tls12'] and checks['transport/cacert.pem']:
        try:
            import client
            settings = client.config()
            # Same transport policy as chat, but no session and no AI request.
            code, data = client.http_json(client.origin(settings['server']) + '/api/mini-ai/health', [], {})
            checks['https_health'] = code == b'200' and data.get('status') == 'ok'
        except Exception:
            pass
    report['online_requested'] = online
    report['transport_verified_on_target'] = all(checks.get(k) for k in
        ['snow_leopard', 'tk_import', 'transport/curl', 'transport/cacert.pem',
         'curl_tls12', 'https_health'])
    return report


if __name__ == '__main__':
    print(json.dumps(diagnose('--online' in sys.argv), indent=2, sort_keys=True))
