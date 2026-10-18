import importlib.util
import json
from pathlib import Path
import zipfile

import pytest

spec = importlib.util.spec_from_file_location('mini_package', Path(__file__).parents[2] / 'clients/package_mini.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_development_package_excludes_credentials_and_preserves_launcher_mode(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    for name in builder.FILES:
        (source / name).write_text('test')
    for name in ('.env', 'config.json', 'test.sqlite', 'token.txt'):
        (source / name).write_text('DO_NOT_DISTRIBUTE')
    archive = builder.package(source, tmp_path / 'client.zip')
    with zipfile.ZipFile(archive) as handle:
        names = handle.namelist()
        assert not any(name.endswith(('.env', '/config.json', '.sqlite', 'token.txt')) for name in names)
        assert all(b'DO_NOT_DISTRIBUTE' not in handle.read(name) for name in names)
        launcher = next(name for name in names if name.endswith('/MacOS/Nexxus'))
        assert handle.getinfo(launcher).external_attr >> 16 & 0o111 == 0o111
        assert 'NO listo' in handle.read('NexxusMiniAI/LEEME.txt').decode()
    with pytest.raises(ValueError, match='ya existe'):
        builder.package(source, archive)


def test_ready_package_rejects_untested_transport(tmp_path):
    evidence = tmp_path / 'report.json'
    evidence.write_text(json.dumps({'os': '26.0', 'transport_verified_on_target': False}))
    with pytest.raises(ValueError, match='Snow Leopard'):
        builder.package(builder.SOURCE, tmp_path / 'client.zip', evidence)


def test_ready_package_checks_exact_tested_binary_hashes(tmp_path):
    evidence = tmp_path / 'report.json'
    evidence.write_text(json.dumps({'os': '10.6.8', 'transport_verified_on_target': True, 'sha256': {}}))
    with pytest.raises(ValueError, match='hash'):
        builder.package(builder.SOURCE, tmp_path / 'client.zip', evidence)
