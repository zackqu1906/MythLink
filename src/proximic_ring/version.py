"""Read the version of the running source tree or frozen application."""
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import sys
import tomllib


def app_version() -> str:
    if getattr(sys, 'frozen', False):
        packaged = Path(__file__).with_name('package-version.txt')
        if packaged.is_file():
            return packaged.read_text(encoding='utf-8').strip()
    else:
        project = Path(__file__).resolve().parents[2] / 'pyproject.toml'
        if project.is_file():
            return tomllib.loads(project.read_text(encoding='utf-8'))['project']['version']
    try:
        return version('mythlink')
    except PackageNotFoundError:
        return '开发版'
