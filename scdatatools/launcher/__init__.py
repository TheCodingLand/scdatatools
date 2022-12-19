import json
import os
import typing
from json.decoder import JSONDecodeError
from pathlib import Path


def get_library_folder(rsilauncher_log_file: typing.Union[Path, str] = None) -> typing.Union[Path, None]:
    """
    Returns a `Path` to the Library Folder of the StarCitizen installation directory, or None if it could not be
    determined
    """
    if rsilauncher_log_file is None:
        rsilauncher_log_file = Path(os.path.expandvars(r"%APPDATA%\rsilauncher\log.log"))
    else:
        rsilauncher_log_file = Path(rsilauncher_log_file)
    if rsilauncher_log_file.is_file():
        for log in rsilauncher_log_file.open("r").read().split("},\n{")[::-1]:
            try:
                log = '{' + log.strip().strip(',').lstrip('{') + "}"
                event = json.loads(log)
                event_type = event.get("info", {}).get("event", "")
                library_folder = None
                if event_type == "INSTALLER@INSTALL":
                    library_folder = Path(event['info']['data']['gameInformation']['libraryFolder'])
                elif event_type == "CHANGE_LIBRARY_FOLDERS":
                    library_folder = Path(event['info']['data']['filePaths'][0])
                if (library_folder is not None and library_folder.is_dir() and
                        (library_folder / 'StarCitizen').is_dir()):
                    return library_folder
            except (KeyError, IndexError, JSONDecodeError, AttributeError):
                pass

    # could not determine the library folder from the launcher log, try the default path
    default_dir = Path(os.path.expandvars(r"%PROGRAMFILES%\Roberts Space Industries"))
    if default_dir.is_dir() and (default_dir / 'StarCitizen').is_dir():
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
