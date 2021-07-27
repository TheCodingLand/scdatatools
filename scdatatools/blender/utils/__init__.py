import os
import shutil
import subprocess
import sys
import typing
from datetime import datetime
from pathlib import Path

try:
    import bpy
except ImportError:
    pass  # not in blender

from . import ui_utils, validation


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
                                f'"{b}" -b --factory-startup --python-expr "import sys, bpy; '
                                f'print(\'VERCHECK\', sys.version.split(' ')[0], '
                                'sys.hexversion, bpy.app.version_string)"',
                                shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                ).stdout.decode('utf-8').split('\n') if _.startswith('VERCHECK')]
                if versions:
                    bv = versions[0][3].rsplit('.', maxsplit=1)[0]
                    blender_installs[bv] = {'path': b, 'compatible': versions[0][2] == pyver, 'pyver': versions[0][1]}
            except (subprocess.CalledProcessError, StopIteration):
                continue

    return blender_installs


def auto_format_sc_data_dir_path(preferences, context):
    """
    This function is called every time the default_sc_data_dir folder path is updated.
    """
    sc_data = Path(preferences.default_sc_data_dir)
    if sc_data.is_dir():
        preferences.incorrect_sc_data_dir_folder_path = False
        if preferences.default_sc_data_dir != sc_data.as_posix():
            preferences.default_sc_data_dir = sc_data.as_posix()
    else:
        preferences.incorrect_sc_data_dir_folder_path = True


def write_to_logfile(log_text, log_name="Output"):
    log_file = bpy.data.texts.get(log_name) or bpy.data.texts.new(log_name)
    log_file.write(f"[{datetime.now()}] {log_text}\n")
    # print(f"[{datetime.now()}] {log_text}")


def search_for_data_dir_in_path(path):
    try:
        if not isinstance(path, Path):
            path = Path(path)
        return Path(*path.parts[:tuple(_.lower() for _ in path.parts).index('data')+1])
    except ValueError:
        return ''
