import sys
import typing
from pathlib import Path

from nubia import command, argument


from scdatatools.blender.addon import install
from scdatatools.blender.utils import available_blender_installations


@command
class Blender:
    """Blender Integration"""

    @command(help="Install the current scdatatools add-on into Blender.")
    @argument(
        "version",
        aliases=["-i"],
        description="Blender version to install the add-on to. Absolute path, or Blender version number if "
        "installed to the default location",
    )
    @argument(
        "list_versions",
        aliases=["-l"],
        description="List detected Blender installations.",
    )
    def install_addon(
        self, version: typing.List[str] = None, list_versions: bool = False
    ):
        if list_versions:
            print(
                "\n".join(
                    f' {v["version"]}:\t{v["path"]}'
                    for k, v in available_blender_installations().items()
                    if v["compatible"]
                )
            )
            return

        if version is None:
            version = set(_["version"] for _ in available_blender_installations().values())

        for v in version:
            print(f"Installed add-on to {str(install(v))}")
