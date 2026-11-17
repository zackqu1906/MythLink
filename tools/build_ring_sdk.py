"""Export the SDK alone, then build its platform wheel without installing anything."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def build(destination):
    destination.mkdir(parents=True, exist_ok=True)
    version = tomllib.loads((ROOT/'sdk/pyproject.toml').read_text())['project']['version']
    name = f'ring-python-sdk-{version}'
    with tempfile.TemporaryDirectory(prefix='ring-sdk-build-') as temporary:
        source = Path(temporary)/name
        shutil.copytree(ROOT/'sdk', source, ignore=shutil.ignore_patterns('__pycache__'))
        for package in ('ring_python_sdk', 'ring_mac_sdk', 'ring_windows_sdk'):
            shutil.copytree(ROOT/'src'/package, source/'src'/package,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (source/'tests').mkdir()
        for filename in ('test_sdk_process.py', 'process_sdk_fakes.py'):
            shutil.copy2(ROOT/'tests'/filename, source/'tests'/filename)
        archive = destination/f'{name}.tar.gz'
        with tarfile.open(archive, 'w:gz') as output:
            output.add(source, arcname=name)
        subprocess.run([sys.executable, '-m', 'pip', 'wheel', '.', '--no-deps',
                        '--no-build-isolation', '--wheel-dir', str(destination)], cwd=source, check=True)
    print(archive)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'dist/sdk')
    build(parser.parse_args().output.resolve())
