import os
import shutil
import subprocess
import sys
import typing
from pathlib import Path


def available_blender_installations(include_paths: typing.List[Path] = None) -> dict:
    """ Return a dictionary of discovered Blender Installations where each value is the `Path` to the installation and
    a `bool` of whether or not the version's Python is compatible with scdatatools.


    ... code-block:: python

        available_blender_installations()
        {'2.93': {'path': WindowsPath('C:/Program Files/Blender Foundation/Blender 2.93'), 'compatible': True}}

    :param include_paths: Additional Blender directories to check
    """
    blender_installs = {}

    pyver = str(sys.hexversion)
    blender = 'blender.exe' if sys.platform == 'win32' else 'blender'

    include_paths = set(include_paths if include_paths is not None else [])
    if shutil.which(blender):
        include_paths.add(Path(shutil.which('blender')).parent)

    if sys.platform == 'win32':
        include_paths.update(_.parent for _ in (Path(os.environ['PROGRAMFILES']) / 'Blender Foundation').rglob(blender))

    for ip in include_paths:
        for b in ip.glob(blender):
            try:
                versions = [_.split() for _ in subprocess.run(
                                f'"{b}" -b --python-expr "import sys, bpy; '
                                f'print(\'VERCHECK\', sys.version.split(' ')[0], '
                                'sys.hexversion, bpy.app.version_string)"',
                                shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                ).stdout.decode('utf-8').split('\n') if 'VERCHECK' in _]
                if versions:
                    bv = versions[0][3].rsplit('.', maxsplit=1)[0]
                    blender_installs[bv] = {'path': b, 'compatible': versions[0][2] == pyver, 'pyver': versions[0][1]}
            except (subprocess.CalledProcessError, StopIteration):
                continue

    return blender_installs