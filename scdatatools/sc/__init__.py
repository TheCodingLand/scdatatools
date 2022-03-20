import sys
import json
from pathlib import Path

from tqdm import tqdm

from rsi.launcher import LauncherAPI
from scdatatools.p4k import P4KFile
from scdatatools.forge import DataCoreBinary
from scdatatools.forge.tags import TagDatabase
from scdatatools.utils import get_size, xxhash32, xxhash32_file
from scdatatools.wwise import WwiseManager
from scdatatools.engine.prefabs import PrefabManager

from .config import Profile
from ..plugins import plugin_manager
from .localization import SCLocalization
from .object_container import ObjectContainerManager

# Files that we will NOT skip the hash for when generating inventory with skip_data_hash
P4K_ALWAYS_HASH_DATA_FILES = [
    ".cfg",
    ".crt",
    ".dpl",
    ".eco",
    ".id",
    ".ini",
    ".xml",
    ".pak",
    ".socpak",
    ".entxml",
]
TRY_VERSION_FILES = [
    "f_win_game_client_release.id",
    "c_hiload_crash_handler.id",
    "c_hiload_crash_handler.id",
    "build_manifest.id",
    "c_win_shader.id",
]


class StarCitizen:
    def __init__(self, game_folder, p4k_file="Data.p4k", p4k_load_monitor=None):
        plugin_manager.setup()  # make sure the plugin manager is setup

        self.branch = self.build_time_stamp = self.config = self.version = None
        self.version_label = self.shelved_change = self.tag = None
        self._fetch_label_success = False
        self._is_loaded = {}
        self._p4k_load_monitor = p4k_load_monitor

        self.game_folder = Path(game_folder).absolute()
        if not self.game_folder.is_dir():
            raise ValueError(f"{self.game_folder} is not a directory")

        self._p4k = None
        self.p4k_file = self.game_folder / p4k_file
        if not self.p4k_file.is_file():
            raise ValueError(f"Could not find p4k file {self.p4k_file}")

        # setup initial empty caches
        self._datacore = (
            self._tag_database
        ) = self._wwise_manager = self._localization = self._profile = None
        self._prefab_manager = self._oc_manager = None

        for ver_file in TRY_VERSION_FILES:
            if (self.game_folder / ver_file).is_file():
                with (self.game_folder / ver_file).open("r") as f:
                    # try to read the version info out of the file
                    try:
                        data = json.loads(f.read())["Data"]
                        self.branch = data.get("Branch", None)
                        self.build_date_stamp = data.get("BuildDateStamp", None)
                        self.build_time_stamp = data.get("BuildTimeStamp", None)
                        self.config = data.get("Config", None)
                        self.version = data.get("RequestedP4ChangeNum", None)
                        self.shelved_change = data.get("Shelved_Change", None)
                        self.tag = data.get("Tag", None)
                        self.version_label = (
                            f"{self.branch}-{self.version}"  # better than nothing
                        )
                        break
                    except Exception as e:  # noqa
                        pass
        else:
            self.version_label = self.game_folder.name
            possible_ver = self.game_folder.name.split("-")[-1].split(".")[-1]
            if possible_ver.isdigit():
                self.version = possible_ver
            sys.stderr.write(f"Warning: Unable to determine version of StarCitizen\n")

    def is_loaded(self, module=None):
        if module is None:
            return self._is_loaded and all(self._is_loaded.values())
        return self._is_loaded.get(module, False)

    def load_all(self):
        """Ensure the p4k, datacore, localization and wwise manager are loaded"""
        assert self.p4k is not None
        assert self.datacore is not None
        assert self.localization is not None
        assert self.wwise is not None
        assert self.tag_database is not None
        assert self.prefab_manager is not None
        assert self.oc_manager is not None

    def generate_inventory(
        self,
        p4k_filters: list = None,
        skip_local=False,
        skip_p4k=False,
        skip_data_hash=False,
    ):
        p4k_filters = p4k_filters or []
        inv = {}
        p4k_path = Path("Data.p4k")

        if not skip_local:
            for f in tqdm(
                self.game_folder.rglob("*"),
                desc="Collecting Local Files",
                unit="files",
                ncols=120,
                unit_scale=True,
            ):
                path = f.relative_to(self.game_folder).as_posix()
                if path in inv:
                    print(f"Error duplicate path: {path}")
                elif f.suffix:
                    if not skip_data_hash or f.suffix in P4K_ALWAYS_HASH_DATA_FILES:
                        inv[path] = (
                            f.stat().st_size,
                            xxhash32_file(f)
                            if f.is_file() and f.name != "Data.p4k"
                            else None,
                        )
                    else:
                        inv[path] = (f.stat().st_size, None)

        if not skip_p4k:
            print("      Opening Data.p4k", end="\r")
            if p4k_filters:
                filenames = self.p4k.search(p4k_filters)
            else:
                filenames = list(self.p4k.NameToInfo.keys())
            for f in tqdm(
                filenames,
                desc="      Reading Data.p4k",
                total=len(filenames),
                unit="files",
                ncols=120,
                unit_scale=True,
            ):
                f = self.p4k.NameToInfo[f]
                path = (p4k_path / f.filename).as_posix()
                if path in inv:
                    print(f"Error duplicate path: {path}")
                elif not f.is_dir():
                    if (
                        not skip_data_hash
                        or Path(f.filename).suffix in P4K_ALWAYS_HASH_DATA_FILES
                    ):
                        fp = self.p4k.open(f, "r")
                        inv[path] = (f.file_size, xxhash32_file(fp))
                        fp.close()
                    else:
                        inv[path] = (f.file_size, None)

        print("      Opening Datacore", end="\r")
        dcb_path = p4k_path / "Data" / "Game.dcb"
        for r in tqdm(
            self.datacore.records,
            desc="      Reading Datacore",
            total=len(self.datacore.records),
            unit="recs",
            ncols=120,
            unit_scale=True,
        ):

            path = (
                f'{(dcb_path / r.filename).with_suffix("").as_posix()}.{r.id.value}.xml'
            )
            try:
                data = self.datacore.dump_record_json(r, indent=None).encode("utf-8")
            except Exception as e:
                data = f"Failed to generate data for record {r.filename}:{r.id.value}. {e}".encode(
                    "utf-8"
                )
                print("\n" + data)
            if path in inv:
                print(f"Error duplicate path: {path}")
            else:
                inv[path] = (get_size(data), xxhash32(data))
        return inv

    @property
    def localization(self):
        if self._localization is None:
            self._localization = SCLocalization(self.p4k)
            self._is_loaded["localization"] = True
        return self._localization

    @property
    def oc_manager(self):
        if self._oc_manager is None:
            self._oc_manager = ObjectContainerManager(self)
            self._is_loaded["oc_manager"] = True
        return self._oc_manager

    @property
    def prefab_manager(self):
        if self._prefab_manager is None:
            self._prefab_manager = PrefabManager(self)
            self._is_loaded["prefab_manager"] = True
        return self._prefab_manager

    @property
    def default_profile(self):
        if self._profile is None:
            self._profile = Profile(self, "Data/Libs/Config/defaultProfile.xml")
        return self._profile

    @property
    def p4k(self):
        if self._p4k is None:
            self._p4k = P4KFile(self.p4k_file, load_monitor=self._p4k_load_monitor)
            self._is_loaded["p4k"] = True
        return self._p4k

    @p4k.setter
    def p4k(self, p4k_file):
        if self.is_loaded("p4k"):
            raise ValueError("Cannot assign a p4k file to an already loaded StarCitizen")

        if not isinstance(p4k_file, P4KFile):
            raise ValueError("p4k must be a P4KFile")

        self._p4k = p4k_file
        self._is_loaded["p4k"] = True

    @property
    def datacore(self):
        if self._datacore is None:
            dcb = self.p4k.getinfo("Data/Game.dcb")
            with dcb.open() as f:
                self._datacore = DataCoreBinary(f.read())
                self._is_loaded["datacore"] = True
        return self._datacore

    @property
    def tag_database(self):
        if self._tag_database is None:
            if self.datacore is None:
                raise ValueError("Could not read the datacore")
            self._tag_database = TagDatabase(dcb=self.datacore)
            self._is_loaded["tag_database"] = True
        return self._tag_database

    @property
    def wwise(self):
        if self._wwise_manager is None:
            self._wwise_manager = WwiseManager(self)
            self._is_loaded["wwise"] = True
        return self._wwise_manager

    def gettext(self, key, language=None):
        return self.localization.gettext(key, language)

    def fetch_version_label(self, rsi_session, force=False) -> str:
        """Try to get the version label from the launcher API for this version. This will only work for currently
        accessible versions. This will also set `self.version_label` to the fetched label.

        :param rsi_session: An authenticated `RSISession`
        :param force: Force update the version label even if it has successfully been fetched already.
        """
        if self._fetch_label_success and not force:
            return self.version_label

        launcher = LauncherAPI(session=rsi_session)
        try:
            for games in launcher.library["games"]:
                if games["id"] == "SC":
                    for version in games["channels"]:
                        if version.get("version", None) == self.version:
                            self.version_label = version["versionLabel"]
                            return self.version_label
            else:
                sys.stderr.write(
                    f"Could not determine version label for {self.version} "
                    f"from library {launcher.library}"
                )
                return ""
        except KeyError:
            return ""
