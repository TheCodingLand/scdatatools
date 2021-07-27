import os
from pathlib import Path


from .utils import install_blender_addon, reload_scdt_blender_modules


try:
    import bpy
except ImportError:
    # Not inside of blender, ignore the blender modules
    modules = []
else:
    from . import preferences
    from scdatatools.blender import blueprints, prefab, materials

    modules = [
        blueprints,
        prefab,
        materials
    ]


ADDON_TEMPLATE = """
# SC DataTools Add-on
# https://gitlab.com/scmodding/frameworks/scdatatools

import sys
import bpy

paths = {path}
sys.path.extend(_ for _ in paths if _ not in sys.path)

bl_info = {{
    "name": "StarCitizen Data Tools",
    "author": "ventorvar",
    "version": (0, 1, 0),
    "blender": (2, 93, 0),
    "location": "View3D > Panel",
    "category": "SC Modding",
    "doc_url": "https://gitlab.com/scmodding/frameworks/scdatatools",
}}

from scdatatools.blender.addon import *
"""


def install(version) -> Path:
    """ Installs the scdatatools add-on into the Blender version `version`. """
    return install_blender_addon(version, 'scdt_addon', ADDON_TEMPLATE)


def register():
    if not modules:
        return

    if (pycharm_debug_port := int(os.environ.get('SCDV_PYCHARM_DEBUG', 0))) > 0:
        import pydevd_pycharm
        print(f'Connecting to pycharm debug on {pycharm_debug_port}')
        pydevd_pycharm.settrace('localhost', port=pycharm_debug_port, stdoutToServer=True, stderrToServer=True)

    reload_scdt_blender_modules()

    for module in modules:
        module.register()

    preferences.register()


def unregister():
    if not modules:
        return

    for module in modules:
        module.unregister()

    preferences.unregister()
