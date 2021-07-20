import importlib
from pathlib import Path

from .utils import install_blender_addon
from ..utils import available_blender_installations

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
    return install_blender_addon(version, 'scdt', ADDON_TEMPLATE)


def register():
    from scdatatools.blender import prefab
    importlib.reload(prefab)
    prefab.register()


def unregister():
    from scdatatools.blender import prefab
    prefab.unregister()
