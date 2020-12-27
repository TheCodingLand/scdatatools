import sys
import json
import shutil
import tempfile
from pathlib import Path

from tqdm import tqdm

from rsi.launcher import LauncherAPI
from scdatatools.p4k import P4KFile
from scdatatools.forge import DataCoreBinary, DataCoreBinaryMMap
from scdatatools.utils import get_size, xxhash32, xxhash32_file

from .config import Profile
from .localization import SCLocalization

# Files that we will NOT skip the hash for when generating inventory with skip_data_hash
P4K_ALWAYS_HASH_DATA_FILES = ['.cfg', '.crt', '.dpl', '.eco', '.id', '.ini', '.xml', '.pak', '.socpak', '.entxml']
TRY_VERSION_FILES = ['f_win_game_client_release.id', 'c_hiload_crash_handler.id', 'c_hiload_crash_handler.id']


class StarCitizen:
    def __init__(self, game_folder, p4k_file='Data.p4k'):
        self.branch = self.build_time_stamp = self.config = self.version = None
        self.version_label = self.shelved_change = self.tag = None
        self._fetch_label_success = False

        self.game_folder = Path(game_folder).absolute()
        if not self.game_folder.is_dir():
            raise ValueError(f'{self.game_folder} is not a directory')

        self._p4k = None
        self.p4k_file = self.game_folder / p4k_file
        if not self.p4k_file.is_file():
            raise ValueError(f'Could not find p4k file {self.p4k_file}')

        self._datacore_tmp = self._datacore = None
        self._localization = self._profile = None

        for ver_file in TRY_VERSION_FILES:
            if (self.game_folder / ver_file).is_file():
                with (self.game_folder / ver_file).open('r') as f:
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
                    except:  # noqa
                        pass
        else:
            sys.stderr.write(
                f"Warning: Unable to determine version of StarCitizen"
            )

    def generate_inventory(self, p4k_filters=[], skip_local=False, skip_p4k=False, skip_data_hash=False):
        inv = {}
        p4k_path = Path('Data.p4k')

        if not skip_local:
            for f in tqdm(self.game_folder.rglob('*'), desc='Collecting Local Files',
                          unit='files', ncols=120, unit_scale=True):
                path = f.relative_to(self.game_folder).as_posix()
                if path in inv:
                    print(f'Error duplicate path: {path}')
                elif f.suffix:
                    if not skip_data_hash or f.suffix in P4K_ALWAYS_HASH_DATA_FILES:
                        inv[path] = (f.stat().st_size,
                                     xxhash32_file(f) if f.is_file() and f.name != 'Data.p4k' else None)
                    else:
                        inv[path] = (f.stat().st_size, None)

        if not skip_p4k:
            print('      Opening Data.p4k', end='\r')
            if p4k_filters:
                filenames = self.p4k.search(p4k_filters)
            else:
                filenames = list(self.p4k.NameToInfo.keys())
            for f in tqdm(filenames, desc='      Reading Data.p4k',
                          total=len(filenames), unit='files', ncols=120, unit_scale=True):
                f = self.p4k.NameToInfo[f]
                path = (p4k_path / f.filename).as_posix()
                if path in inv:
                    print(f'Error duplicate path: {path}')
                else:
                    if not skip_data_hash or Path(f.filename).suffix in P4K_ALWAYS_HASH_DATA_FILES:
                        fp = self.p4k.open(f, 'r')
                        inv[path] = (f.file_size, xxhash32_file(fp))
                        fp.close()
                    else:
                        inv[path] = (f.file_size, None)

        print('      Opening Datacore', end='\r')
        dcb_path = p4k_path / 'Data' / 'Game.dcb'
        for r in tqdm(self.datacore.records, desc='      Reading Datacore',
                      total=len(self.datacore.records), unit='recs', ncols=120, unit_scale=True):
            path = f'{(dcb_path / r.filename).as_posix()}:{r.id.value}'
            try:
                data = self.datacore.dump_record_json(r, indent=None).encode('utf-8')
            except Exception as e:
                data = f'Failed to generate data for record {r.filename}:{r.id.value}. {e}'.encode('utf-8')
                print('\n' + data)
            if path in inv:
                print(f'Error duplicate path: {path}')
            else:
                inv[path] = (get_size(data), xxhash32(data))
        return inv

    @property
    def localization(self):
        if self._localization is None:
            self._localization = SCLocalization(self.p4k)
        return self._localization

    @property
    def default_profile(self):
        if self._profile is None:
            self._profile = Profile(self, 'Data/Libs/Config/defaultProfile.xml')
        return self._profile

    @property
    def p4k(self):
        if self._p4k is None:
            self._p4k = P4KFile(self.p4k_file)
        return self._p4k

    @property
    def datacore(self):
        if self._datacore is None:
            dcb = self.p4k.search('*Game.dcb')
            if len(dcb) != 1:
                raise ValueError('Could not determine the location of the datacore')
            with self.p4k.open(dcb[0]) as f:
                self._datacore_tmp = tempfile.TemporaryFile()
                shutil.copyfileobj(f, self._datacore_tmp)
                self._datacore_tmp.seek(0)
                self._datacore = DataCoreBinary(DataCoreBinaryMMap(self._datacore_tmp))
        return self._datacore

    def gettext(self, key, language=None):
        return self.localization.gettext(key, language)

    def fetch_version_label(self, rsi_session, force=False) -> str:
        """ Try to get the version label from the launcher API for this version. This will only work for currently
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
