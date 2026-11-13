import json
from pathlib import Path
import zipfile

import pytest

from proximic_ring.firmware.bundle import DEFAULT_DIRECTORY, inspect_package, load_bundle, prepare_bundle
from ring_python_sdk.ota.image import load_ota_image
from ring_python_sdk.ota.types import OtaError


def test_shipped_firmware_and_independent_build_override(tmp_path):
    default = load_bundle()
    assert default.version and default.image_version and default.size > 0
    assert len(default.sha256) == 64
    before = (DEFAULT_DIRECTORY / 'bundle.json').read_bytes()
    staged = prepare_bundle(default.path, tmp_path / 'stage')
    assert staged.image_hash == default.image_hash
    assert (DEFAULT_DIRECTORY / 'bundle.json').read_bytes() == before
    metadata = staged.path.parent / 'bundle.json'
    metadata.write_text('{}')
    with pytest.raises(ValueError, match='校验失败'):
        load_bundle(staged.path.parent)


def test_replacing_default_reads_display_version_from_package(tmp_path):
    package = tmp_path / 'new.zip'
    with zipfile.ZipFile(load_bundle().path) as source, zipfile.ZipFile(package, 'w') as dest:
        for name in source.namelist():
            data = source.read(name)
            if name == 'build-info.json':
                info = json.loads(data)
                info['displayVersion'] = '2.1.6-test.next'
                data = json.dumps(info).encode()
            dest.writestr(name, data)
    result = prepare_bundle(package, tmp_path / 'default')
    assert load_bundle(result.path.parent).version == '2.1.6-test.next'


@pytest.mark.parametrize('mutation', ['soc', 'image_index', 'hash'])
def test_wrong_target_or_corrupt_image_never_replaces_default(tmp_path, mutation):
    directory = tmp_path / 'default'
    current = prepare_bundle(load_bundle().path, directory)
    before = current.path.read_bytes()
    bad = tmp_path / 'bad.zip'
    with zipfile.ZipFile(current.path) as source, zipfile.ZipFile(bad, 'w') as dest:
        for name in source.namelist():
            data = source.read(name)
            if name == 'manifest.json' and mutation != 'hash':
                info = json.loads(data)
                info['files'][0][mutation] = 'wrong'
                data = json.dumps(info).encode()
            elif name.endswith('.signed.bin') and mutation == 'hash':
                damaged = bytearray(data); damaged[2050] ^= 1; data = bytes(damaged)
            dest.writestr(name, data)
    with pytest.raises((ValueError, OtaError)):
        prepare_bundle(bad, directory)
    assert current.path.read_bytes() == before
    assert load_bundle(directory).sha256 == current.sha256
