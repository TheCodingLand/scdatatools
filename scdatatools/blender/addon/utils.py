import os
import sys
from pathlib import Path


def install_blender_addon(blender_ver, addon_name, addon_template) -> Path:
    """ Installs a Blender Addon `addon_name` to the appropriate location for `blender_ver` using `addon_template`.
    Returns the path to the newly installed addon """

    if sys.platform == 'win32':
        addon_py = Path(f"{os.environ['APPDATA']}/Blender Foundation/blender/"
                        f"{blender_ver}/scripts/addons/{addon_name}.py").expanduser()
    elif sys.platform == 'linux':
        addon_py = Path(f"~/.config/blender/{blender_ver}/scripts/addons/{addon_name}.py").expanduser()
    elif sys.platform == 'darwin':
        addon_py = Path(f"~/Library/Application Support/Blender/"
                        f"{blender_ver}/scripts/addons/{addon_name}.py").expanduser()
    else:
        raise ValueError(f'Unsupported platform {sys.platform}')

    addon_py.parent.mkdir(parents=True, exist_ok=True)
    with addon_py.open('w') as addon:
        addon.write(addon_template.format(path=sorted(sys.path)))
    return addon_py
