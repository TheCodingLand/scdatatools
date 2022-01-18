import os
import json
import typing
from pathlib import Path
from json.decoder import JSONDecodeError


def get_library_folder() -> typing.Union[Path, None]:
    """
    Returns a `Path` to the Library Folder of the StarCitizen installation directory, or None if it could not be
    determined
    """
    logfile = Path(os.path.expandvars(r"%APPDATA%\rsilauncher\log.log"))
    if logfile.is_file():
        for log in logfile.open("r").read().split("} },")[::-1]:
            try:
                return Path(json.loads(log + "}}")["info"]["data"]["filePaths"][0])
            except (KeyError, IndexError, JSONDecodeError):
                pass

    # could not determine the library folder from the launcher log, try the default path
    default_dir = Path(os.path.expandvars(r"%PROGRAMFILES%\Roberts Space Industries"))
    if default_dir.is_dir():
        return default_dir

    return None


def get_installed_sc_versions() -> typing.Dict[str, Path]:
    """Returns a dictionary of the currently available installations of Star Citizen"""
    vers = {}
    lib_folder = get_library_folder()
    if lib_folder is None:
        return vers

    if (lib_folder / "StarCitizen" / "LIVE" / "Data.p4k").is_file():
        vers["LIVE"] = lib_folder / "StarCitizen" / "LIVE"

    if (lib_folder / "StarCitizen" / "PTU" / "Data.p4k").is_file():
        vers["PTU"] = lib_folder / "StarCitizen" / "PTU"

    return vers
