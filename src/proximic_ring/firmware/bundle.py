from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from ring_python_sdk.ota.image import load_ota_image

DEFAULT_DIRECTORY = Path(__file__).resolve().parents[1] / 'assets' / 'firmware'


@dataclass(frozen=True)
class FirmwareBundle:
    path: Path
    version: str
    image_version: str
    sha256: str
    image_hash: str
    size: int


def inspect_package(path: Path) -> FirmwareBundle:
    image = load_ota_image(path)
    with zipfile.ZipFile(path) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError('固件包包含重复文件')
        for name in ('manifest.json', 'build-info.json'):
            if name in archive.namelist() and archive.getinfo(name).file_size > 65536:
                raise ValueError('固件元数据过大')
        manifest = json.loads(archive.read('manifest.json'))
        files = manifest.get('files', [])
        if len(files) != 1:
            raise ValueError('仅支持单个应用固件')
        entry = files[0]
        if (entry.get('type') != 'application' or entry.get('soc') != 'nrf54l15'
                or entry.get('board') != 'nrf54l15dk' or str(entry.get('image_index')) != '0'
                or entry.get('load_address') != 0xE000
                or str(entry.get('slot_index_primary')) != '1'
                or str(entry.get('slot_index_secondary')) != '2'):
            raise ValueError('固件包与当前 Ringo nRF54L15 布局不兼容')
        if (entry.get('file') != image.source_name or entry.get('size') != len(image.data)
                or entry.get('version_MCUBOOT') != image.version):
            raise ValueError('固件清单与镜像不一致')
        info = json.loads(archive.read('build-info.json')) if 'build-info.json' in archive.namelist() else {}
        if info.get('imageSha256') and info['imageSha256'] != hashlib.sha256(image.data).hexdigest():
            raise ValueError('固件 build-info 校验失败')
        if info.get('imageVersion') and info['imageVersion'] != image.version:
            raise ValueError('固件 build-info 版本不一致')
    return FirmwareBundle(path, info.get('displayVersion') or image.version, image.version,
                          hashlib.sha256(path.read_bytes()).hexdigest(), image.hash_hex, len(image.data))


def load_bundle(directory: Path = DEFAULT_DIRECTORY) -> FirmwareBundle:
    metadata = json.loads((directory / 'bundle.json').read_text(encoding='utf-8'))
    result = inspect_package(directory / 'ring-firmware.zip')
    if metadata != bundle_metadata(result):
        raise ValueError('随包固件校验失败，请重新打包应用')
    return result


def bundle_metadata(bundle):
    return dict(version=bundle.version, image_version=bundle.image_version,
                sha256=bundle.sha256, image_hash=bundle.image_hash, size=bundle.size)


def prepare_bundle(source: Path, destination: Path) -> FirmwareBundle:
    """Validate first; replace only the explicit destination. Never flash a device."""
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        stage = Path(temporary)
        shutil.copyfile(source, stage / 'ring-firmware.zip')
        result = inspect_package(stage / 'ring-firmware.zip')
        (stage / 'bundle.json').write_text(json.dumps(bundle_metadata(result), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        for name in ('ring-firmware.zip', 'bundle.json'):
            (stage / name).replace(destination / name)
    return load_bundle(destination)
