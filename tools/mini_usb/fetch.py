"""Download only pinned MacPorts archives. Never executes downloaded binaries."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
from urllib.parse import unquote


def download(url, path, digest=None):
    if not url.startswith(('https://packages.macports.org/', 'https://distfiles.macports.org/',
                               'https://raw.githubusercontent.com/macports/macports-ports/')):
        raise ValueError('Unapproved download origin')
    if not path.exists():
        subprocess.run(['curl', '-q', '-fLsS', '--proto', '=https', '--max-time', '180',
                        url, '-o', str(path)], check=True)
    if digest and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('Checksum mismatch: ' + path.name)


def fetch(root, definitions):
    cache = root / 'archives'
    cache.mkdir(parents=True, exist_ok=True)
    prefix = root / 'ports'
    if prefix.exists():
        raise ValueError('Use a fresh work directory: ports/ already exists')
    prefix.mkdir()
    key = definitions / 'macports-pubkey.pem'
    records = json.loads((definitions / 'ports-lock.json').read_text())
    for record in records:
        filename = unquote(record['url'].rsplit('/', 1)[-1])
        path = cache / filename
        download(record['url'], path, record['sha256'])
        signature = cache / (filename + '.rmd160')
        download(record['url'] + '.rmd160', signature)
        subprocess.run(['openssl', 'dgst', '-ripemd160', '-verify', str(key),
                        '-signature', str(signature), str(path)], check=True,
                       stdout=subprocess.DEVNULL)
        with tarfile.open(path) as archive:
            members = []
            for member in archive:
                member.name = member.name.removeprefix('./')
                if member.issym() and member.linkname.startswith('/opt/local/'):
                    member.linkname = os.path.relpath(prefix / member.linkname.lstrip('/'),
                                                     (prefix / member.name).parent)
                if (member.name.startswith('opt/local/') and '/man/' not in member.name
                        and '..' not in PurePosixPath(member.name).parts):
                    members.append(member)
            archive.extractall(prefix, members=members, filter='data')
    (root / 'ports-lock.json').write_text(json.dumps(records, indent=2) + '\n')
