"""Tag the native model payload for its supported host platform."""
import platform

from setuptools import setup
from setuptools.command.bdist_wheel import bdist_wheel


class SDKWheel(bdist_wheel):
    def finalize_options(self):
        super().finalize_options()
        self.root_is_pure = False
        if platform.system() == 'Darwin' and platform.machine() == 'arm64':
            self.plat_name = 'macosx_13_0_arm64'
        elif platform.system() == 'Windows' and platform.machine().lower() in {'amd64', 'x86_64'}:
            self.plat_name = 'win_amd64'
        else:
            raise RuntimeError('Build the SDK wheel on Apple Silicon macOS or Windows x64')

    def get_tag(self):
        return 'py3', 'none', self.plat_name


setup(cmdclass={'bdist_wheel': SDKWheel})
