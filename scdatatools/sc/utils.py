import re
import json
import time
import shutil
import typing
import logging
import subprocess
from pathlib import Path
import concurrent.futures
from xml.etree import ElementTree

from pyquaternion import Quaternion

from scdatatools.cry.model.ivo import Ivo
from scdatatools.cry.model.chcr import ChCr
from scdatatools.utils import SCJSONEncoder
from scdatatools.forge.dftypes import Record
from scdatatools.cry.model import chunks as ChCrChunks
from scdatatools.forge.dco import dco_from_guid, DataCoreObject
from scdatatools.sc.textures import tex_convert, collect_and_unsplit, is_glossmap
from scdatatools.utils import etree_to_dict, norm_path, dict_search
from scdatatools.cry.model.utils import Vector3D
from scdatatools.cry.cryxml import dict_from_cryxml_file, dict_from_cryxml_string, CryXmlConversionFormat

logger = logging.getLogger(__name__)

PROCESS_FILES = [
    'mtl', 'chrparams', 'cga', 'cgam', 'cgf', 'cgfm', 'soc', 'xml', 'entxml', 'chr', 'rmp', 'dba', 'animevents',
    'skin', 'skinm', 'cdf'
]
CGF_CONVERTER_MODEL_EXTS = ['.cga', '.cgf', '.chr', '.skin']
CGF_CONVERTER_TIMEOUT = 5 * 60   # assume cgf converter is stuck after this much time
CGF_CONVERTER_DEFAULT_OPTS = '-skipphysproxy -skipproxy -skipproxymat -skipshield -group -smooth -notex'
RECORDS_BASE_PATH = Path('libs/foundry/records/')
SHIP_ENTITIES_PATH = RECORDS_BASE_PATH / 'entities/spaceships'
TEXCONV_IGNORE = ['_ddna']
CGF_CONVERTER = shutil.which('cgf-converter')
RECORD_KEYS_WITH_PATHS = [
    # all keys are lowercase to ignore case while matching
    '@file',  # @File mtl
    '@path',  # @Path/@path chrparams, entxml, soc_cryxml, mtl
    '@texture',  # soc_cryxml
    '@cubemaptexture',  # @cubemapTexture soc_cryxml
    '@externallayerfilepath',  # @externalLayerFilePath soc_cryxml
    'animationdatabase',  # AnimationDatabase Ship Entity record in 'SAnimationControllerParams'
    'animationcontroller',  # AnimationController Ship Entity record in 'SAnimationControllerParams'
    'voxeldatafile',  # voxelDataFile Ship Entity record in 'SVehiclePhysicsGridParams'
]
RECORD_KEYS_WITH_AUDIO = [
    'audioTrigger'
]
DEFAULT_ROTATION = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


class Geometry(dict):
    def __init__(self, name, geom_file, pos=None, rotation=None, scale=None, materials=None, attrs=None, parent=None):
        super().__init__()
        self['name'] = name
        self['geom_file'] = geom_file
        self['instances'] = {}
        self['materials'] = set()
        self.add_materials(materials or [])
        if pos:
            self.add_instance('', pos, rotation, scale)
        self['attrs'] = attrs or {}
        self.parent = parent

    def add_materials(self, mats):
        # ensure material files have the correct suffix
        if not isinstance(mats, (list, tuple, set)):
            mats = [mats]
        self['materials'].update(Path(mat).with_suffix('.mtl').as_posix() for mat in mats if mat)

    def add_instance(self, name, pos, rotation=None, scale=None, materials=None, attrs=None):
        if not name:
            name = str(len(self['instances']))
        self.add_materials(materials or [])
        self['instances'][name] = {
            'pos': pos,
            'rotation': rotation if rotation is not None else DEFAULT_ROTATION,
            'scale': scale or Vector3D(1, 1, 1),
            'materials': materials or [],
            'attrs': attrs or {}
        }

    def add_sub_geometry(self, name, *args, **kwargs):
        self['sub_geometry'][name] = Geometry(name, parent=self, *args, **kwargs)

    def __hash__(self):
        return hash(tuple(self))


# TODO: This is _very_ much a hacked together proof-of-concept/experiment. Once all of the individual components have
#   been identified, some thought needs to go into how to make this more maintainable, and possibly more performant.
#   Thoughts should also go into how to extract other entities (weapons, armor, buildings, etc.)
#
# TODO: General to-dos,
#   - properly handle `cdf` references (describes a model and attachments to that model with rigging
#

class EntityExtractor:
    def _reset(self):
        self._cache = {
            'files_to_extract': set(),
            'files_to_process': set(),
            'found_records': set(),
            'audio_to_extract': set(),
            'records_to_process': set(),
            'item_ports': {},
            'bone_names': set(),
            'exclude': set(),
            'geometry': {},
            'found_geometry': {},
            'record_geometry': {},
        }

    def __init__(self, sc, entity: typing.Union[DataCoreObject, Record]):
        """
        Process and extract a `ShipEntity` records (typically found in `entities/spaceships`) which contain all the
        information pertaining to a ship in game, the models, components, object containers, etc. This utility will
        recursively parse this record, and all referenced objects within it to find and extract all data pertaining to the
        ship.

        :param sc: :class:`StarCitizen` instance
        :param entity: The DataCore object of the `Entity` to extract
        """
        self.sc = sc
        self.entity = entity if isinstance(entity, DataCoreObject) else dco_from_guid(self.sc.datacore, entity.id)
        self.monitor = None
        self.outdir = None
        self.convert_cryxml_fmt = 'xml'
        self.skip_lods = True

        # Track every p4k file we need to extract
        self._cache = {}
        self._reset()

        # build up records by name as a quick way to look up records later. we're ok with collisions here as the records
        # we need will be uniquely named
        self._records_by_name = {_.name: _ for _ in self.sc.datacore.records}

        # create a convenience quick lookup for base filenames
        self._p4k_files = set(_.lower().split('.', maxsplit=1)[0] for _ in self.sc.p4k.namelist())

    def _add_file_to_extract(self, path: typing.Union[str, list, tuple, set, Path]):
        if not path:
            return
        if isinstance(path, (list, tuple, set)):
            for p in path:
                self._add_file_to_extract(p)
            return
        elif isinstance(path, Path):
            path = path.as_posix()
        path = path.lower()

        path = norm_path(f'{"" if path.startswith("data") else "data/"}{path}')
        if '.' not in path:
            # add whole dir
            if path not in self._cache['files_to_extract']:
                self._cache['files_to_extract'].add(path)
                self.log(f'+ dir ex: {path}')
        else:
            base, ext = path.split('.', maxsplit=1)
            if base not in self._p4k_files:
                self.log(f'could not find file in P4K: {path}', logging.WARNING)
                return

            if self.skip_lods and base[-5:-1] == '_lod':  # skip things ending with `_lod[0-9]`
                self._cache['exclude'].add(path)
                return

            if base not in self._cache['files_to_extract']:
                self._cache['files_to_extract'].add(base)
                self.log(f'+ file ex: {base}')
            if ext in PROCESS_FILES:
                self._cache['files_to_process'].add(path)
            else:
                # second split handles things like .dds.1
                if ext.split('.')[0] not in ['dds', 'tif', 'socpak', 'brmp', 'obj']:
                    # TODO: figure out what BRMP files are
                    self.log(f'unhandled file {ext} {path}', logging.WARNING)
                    # TODO: add support for gfx files:
                    #      'data/ui/environmentalscreens/ships/idris/fluff/swf/9x16-small_securitycode.gfx'

    def _add_record_to_extract(self, guid: typing.Union[str, list, tuple, set]):
        if not guid:
            return
        if isinstance(guid, (list, tuple, set)):
            for g in guid:
                self._add_record_to_extract(g)
            return

        try:
            record = dco_from_guid(self.sc.datacore, guid)
        except KeyError:
            return self.log(f'record {guid} does not exist', logging.WARNING)

        if record.guid not in self._cache['found_records']:
            self.log(f'+ record: {Path(record.filename).relative_to(RECORDS_BASE_PATH).as_posix()}')
            self._cache['found_records'].add(record.guid)
            self._cache['records_to_process'].add(record)
            outrec = self.outdir / 'Data' / record.filename.replace('.xml', f'.{self.convert_cryxml_fmt}')
            outrec.parent.mkdir(exist_ok=True, parents=True)
            with outrec.open('w') as out:
                if self.convert_cryxml_fmt == 'xml':
                    out.write(record.to_xml())
                else:
                    out.write(record.to_json())

    def _add_audio_to_extract(self, trigger_name):
        if trigger_name in self.sc.wwise.triggers:
            self._cache['audio_to_extract'].add(trigger_name)

    def _handle_ext_geom(self, rec, obj, tags=''):
        if obj.name == 'SGeometryDataParams':
            mtl = obj.properties['Material'].properties['path']
            self._add_file_to_extract(mtl)
            p = None
            tints_dir = self.outdir / 'tint_palettes' / self.entity.name
            try:
                tint_id = str(obj.properties['Palette'].properties['RootRecord'])
                if tint_id != '00000000-0000-0000-0000-000000000000':
                    p = self.sc.datacore.records_by_guid[tint_id]
                    with (tints_dir / f'{p.name}.json').open('w') as f:
                        f.write(self.sc.datacore.dump_record_json(p))
                    self._add_file_to_extract(p.properties['root'].properties['decalTexture'])
            except Exception as e:
                self.log(f'could not dump tint: {e}', logging.WARNING)

            geom_path = obj.properties['Geometry'].properties['path']
            if geom_path:
                attrs = {'tags': tags}
                if p is not None:
                    attrs['palette'] = (tints_dir / f'{p.name}.json').as_posix()
                geom, created = self._get_or_create_geom(geom_path, create_params={
                    'attrs': attrs, 'pos': Vector3D() if rec.guid == self.entity.guid else None,
                    'materials': mtl
                })
                self._cache['record_geometry'].setdefault(rec.guid, {}).setdefault(tags, set()).add(geom['name'])

        if 'Geometry' in obj.properties:
            self._handle_ext_geom(rec, obj.properties['Geometry'], obj.properties.get('Tags', ''))
        if 'SubGeometry' in obj.properties:
            for sg in obj.properties.get('SubGeometry', []):
                self._handle_ext_geom(rec, sg, obj.properties.get('Tags', ''))
        if 'Material' in obj.properties:
            self._handle_ext_geom(rec, obj.properties['Material'])
        if 'path' in obj.properties:
            self._add_file_to_extract(obj.properties['path'])

    def _handle_component_loadouts(self, rec, obj):
        try:
            for entry in obj.properties['loadout'].properties.get('entries', []):
                try:
                    if entry.properties['entityClassName']:
                        ipe = self._records_by_name[entry.properties["entityClassName"]].id.value
                        self._cache['item_ports'].setdefault(entry.properties['itemPortName'], set()).add(ipe)
                        self._cache['bone_names'].add(entry.properties['itemPortName'])
                        self._add_record_to_extract(ipe)
                    if entry.properties['loadout']:
                        self._handle_component_loadouts(rec, entry)
                except Exception as e:
                    self.log(f'processing component SEntityComponentDefaultLoadoutParams: {repr(e)}', logging.ERROR)
        except Exception as e:
            self.log(f'processing component SEntityComponentDefaultLoadoutParams: {obj} {repr(e)}', logging.ERROR)

    def _handle_soc(self, bone_name, soc):
        for chunk in soc.chunks.values():
            if isinstance(chunk, ChCrChunks.IncludedObjects):
                self._add_file_to_extract(chunk.filenames)
                materials = chunk.materials
                for obj in chunk.objects:
                    if isinstance(obj, ChCrChunks.IncludedObjectType1):
                        geom, _ = self._get_or_create_geom(obj.filename)
                        self._cache['found_geometry'][geom['name']].add_instance(
                            '', pos=obj.pos, rotation=obj.rotation, scale=obj.scale,
                            materials=materials, attrs={'bone_name': bone_name}
                        )
            if isinstance(chunk, ChCrChunks.CryXMLBChunk):
                # TODO: read cryxmlb chunk, it seems to be all related to lighting/audio?
                d = chunk.dict()
                # Root can be Entities or SCOC_Entities
                entities = d.get('Entities', d.get('SCOC_Entities', {})).get('Entity')
                if isinstance(entities, dict):
                    entities = [entities]  # only one entity in this cryxmlb
                for entity in entities:
                    try:
                        if 'EntityGeometryResource' in entity.get('PropertiesDataCore', {}):
                            geom, _ = self._get_or_create_geom(
                                entity['PropertiesDataCore']['EntityGeometryResource']
                                ['Geometry']['Geometry']['Geometry']['@path']
                            )
                            w, x, y, z = (float(_) for _ in entity.get('@Rotate', '1,0,0,0').split(','))
                            self._cache['found_geometry'][geom['name']].add_instance(
                                name=entity['@Name'],
                                pos=Vector3D(
                                    *(float(_) for _ in entity['@Pos'].split(','))) if '@Pos' in entity else Vector3D(),
                                rotation=Quaternion(x=x, y=y, z=z, w=w),
                                materials=[entity.get("@Material", '')],
                                attrs={
                                    'bone_name': bone_name,
                                    'layer': entity['@Layer']
                                }
                            )
                    except Exception as e:
                        self.log(f'Failed to parse soc cryxmlb entity "{entity["@Name"]}": {repr(e)}')

    def _handle_vehicle_components(self, rec, vc):
        for prop in ['landingSystem']:
            if vc.properties.get(prop):
                self._add_record_to_extract(vc.properties[prop])
        for prop in ['physicsGrid']:
            if prop in vc.properties:
                self._search_record(vc.properties[prop])
        if vc.properties.get('vehicleDefinition'):
            self._add_file_to_extract(vc.properties['vehicleDefinition'])
        if vc.properties.get('objectContainers'):
            for oc in vc.properties['objectContainers']:
                p4k_path = norm_path(oc.properties["fileName"])
                try:
                    self._add_file_to_extract(p4k_path)  # extract the socpak itself
                    archive = self.sc.p4k.NameToInfoLower.get(f'data/{p4k_path}'.lower())
                    if archive is None:
                        self.log(f'socpak not found in p4k: "{p4k_path}"', logging.WARNING)
                        continue
                    self._add_file_to_extract([_.filename for _ in archive.filelist])
                    p4k_path = Path(p4k_path)
                    soc_path = p4k_path.parent / p4k_path.stem / f'{p4k_path.stem}.soc'
                    soc = self.sc.p4k.NameToInfoLower.get(f'data/{soc_path.as_posix()}')
                    if soc is not None:
                        soc = ChCr(soc.open().read())
                        self._cache['bone_names'].add(oc.properties['boneName'])
                        self._handle_soc(oc.properties['boneName'], soc)
                except Exception as e:
                    self.log(f'failed to process object container "{p4k_path}": {repr(e)}', logging.ERROR)
                    raise

    def _handle_audio_component(self, rec, ac):
        print("TODO: 'ShipAudioComponentParams'")
        print("TODO: 'AudioPassByComponentParams'")
        # TODO: dict search component for audioTrigger?

    def _handle_landinggear(self, r):
        for gear in r.record.properties['gears']:
            self._handle_ext_geom(r, gear.properties['geometry'])
            geom, _ = self._get_or_create_geom(gear.properties['geometry'].properties['path'])
            self._cache['item_ports'].setdefault(gear.properties['bone'], set()).add(geom['name'])
            self._cache['bone_names'].add(gear.properties['bone'])

    def _search_record(self, r):
        """ This is a brute-force method of extracting related files from a datacore record. It does no additional
        processing of the record, if there is specific data that should be extracted a different method should be
        implemented and used for that record type. """
        d = self.sc.datacore.record_to_dict(r)
        self._add_file_to_extract(dict_search(d, RECORD_KEYS_WITH_PATHS, ignore_case=True))

    def _search_record_audio(self, r):
        d = self.sc.datacore.record_to_dict(r)
        self._add_audio_to_extract(dict_search(d, RECORD_KEYS_WITH_AUDIO, ignore_case=True))

    def _process_record(self, r):
        if r.type == 'EntityClassDefinition':
            if 'SGeometryResourceParams' in r.components:
                self._handle_ext_geom(r, r.components['SGeometryResourceParams'])
            if 'SEntityComponentDefaultLoadoutParams' in r.components:
                self._handle_component_loadouts(r, r.components['SEntityComponentDefaultLoadoutParams'])
            if 'VehicleComponentParams' in r.components:
                self._handle_vehicle_components(r, r.components['VehicleComponentParams'])
            if 'ShipAudioComponentParams' in r.components:
                self._handle_audio_component(r, r.components['ShipAudioComponentParams'])
            if 'AudioPassByComponentParams' in r.components:
                self._handle_audio_component(r, r.components['AudioPassByComponentParams'])

            audio_comps = [
                'EntityPhysicalAudioParams'
            ]
            for comp in audio_comps:
                if 'comp' in r.components:
                    self._search_record_audio(r, r.components[comp])

            additional_comps = [
                'SAnimationControllerParams',
            ]
            for comp in additional_comps:
                if 'comp' in r.components:
                    self._search_record(r.components[comp])
        elif r.type == 'VehicleLandingGearSystem':
            self._handle_landinggear(r)
        else:
            self.log(f'unhandled type: {r}', logging.WARNING)

        # TODO: handle
        #   - AudioPassByComponentParams
        #   - SAnimationControllerParams

    def _get_or_create_geom(self, geom_path, create_params=None) -> (Geometry, bool):
        created = False
        if not isinstance(geom_path, Path):
            geom_path = Path(geom_path)
        geom_name = geom_path.as_posix().lower()
        if geom_path.parts[0].lower() == 'data':
            geom_name = geom_name[5:]
            geom_path = Path(*geom_path.parts[1:])
        if geom_name not in self._cache['found_geometry']:
            self._cache['found_geometry'][geom_name] = Geometry(name=geom_name, geom_file=geom_path,
                                                                **(create_params or {}))
            created = True
        return self._cache['found_geometry'][geom_name], created

    def _process_p4k_file(self, path):
        ext = path.split('.', maxsplit=1)[1]
        try:
            p4k_info = self.sc.p4k.NameToInfoLower[path.lower()]
        except KeyError:
            self.log(f'Kind find p4k file to process, how did we get here? {path}', logging.ERROR)
            return
        self.log(f'process: ({ext}) {p4k_info.filename}')
        try:
            if ext in ['mtl', 'chrparams', 'entxml', 'rmp', 'animevents', 'cdf']:
                self._add_file_to_extract(dict_search(dict_from_cryxml_file(self.sc.p4k.open(p4k_info)),
                                                      RECORD_KEYS_WITH_PATHS, ignore_case=True))
            elif ext in ['cga', 'cgam', 'cgf', 'cgfm', 'chr', 'soc', 'dba', 'skin', 'skinm']:
                raw = self.sc.p4k.open(p4k_info).read()
                c = Ivo(raw) if raw.startswith(b'#ivo') else ChCr(raw)
                for chunk in c.chunks.values():
                    if isinstance(chunk, ChCrChunks.CryXMLBChunk):
                        x = dict_from_cryxml_string(chunk.data)
                        self._add_file_to_extract(dict_search(x, RECORD_KEYS_WITH_PATHS, ignore_case=True))
                        # Material keys don't have the extension
                        self._add_file_to_extract([f'{_}.mtl' for _ in dict_search(x, '@material', ignore_case=True)])

                        # write out the extracted CryXMLB as json
                        out_path = self.outdir / f"{p4k_info.filename}.cryxml.json"
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        with out_path.open('w') as o:
                            json.dump(x, o, indent=4)
                    elif isinstance(chunk, ChCrChunks.JSONChunk):
                        x = chunk.dict()
                        self._add_file_to_extract(dict_search(x, RECORD_KEYS_WITH_PATHS, ignore_case=True))
                        out_path = self.outdir / f"{p4k_info.filename}.json"
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        with out_path.open('w') as o:
                            json.dump(x, o, indent=4)
                    elif isinstance(chunk, (ChCrChunks.MtlName, ChCrChunks.MaterialName900)):
                        mtl_path = Path(f'{chunk.name}').with_suffix('.mtl')
                        # self._add_file_to_extract([_ for _ in self.sc.p4k.NameToInfoLower.keys()
                        #                            if _.startswith(mtl_path) and _.endswith('.mtl')])
                        # mtl_path = f'Data/{chunk.name}'
                        geom, _ = self._get_or_create_geom(path)
                        geom.add_materials(mtl_path)
                        self._add_file_to_extract(mtl_path)
                        # for _ in self.sc.p4k.NameToInfoLower.keys():
                        #     if _.startswith(mtl_path) and _.endswith('.mtl'):
                        #         self._cache['found_geometry'][geom['name']].add_materials(_)
                        #         self._add_file_to_extract(_)
                    elif isinstance(chunk, ChCrChunks.IncludedObjects):
                        self._add_file_to_extract(chunk.filenames)
            elif ext in 'xml':
                raw = self.sc.p4k.open(p4k_info).read()
                if raw.startswith(b'CryXmlB'):
                    x = dict_from_cryxml_string(raw)
                else:
                    x = etree_to_dict(ElementTree.fromstring(raw))
                self._add_file_to_extract(dict_search(x, RECORD_KEYS_WITH_PATHS, ignore_case=True))
            else:
                self.log(f'unhandled p4k file: {path}', logging.WARNING)
        except Exception as e:
            self.log(f'processing {path}: {e}', logging.ERROR)
            raise

    def log(self, msg, level=logging.INFO):
        if self.monitor is not None:
            if level != logging.INFO:
                self.monitor(f'{logging.getLevelName(level)}: {msg}')
            else:
                self.monitor(msg)
        logger.log(level, msg)

    def extract(self, outdir: typing.Union[Path, str], remove_outdir: bool = False,
                convert_cryxml_fmt: CryXmlConversionFormat = 'xml', skip_lods: bool = True,
                auto_unsplit_textures: bool = True, auto_convert_textures: bool = False,
                report_tex_conversion_errors: bool = False, convert_dds_fmt: str = 'png',
                extract_sounds: bool = True, auto_convert_models: bool = False,
                cgf_converter_opts: str = CGF_CONVERTER_DEFAULT_OPTS, auto_convert_sounds: bool = False,
                ww2ogg: str = '', revorb: str = '', cgf_converter: str = '',
                exclude: typing.List[str] = None, monitor: typing.Callable = None) -> typing.List[str]:
        """
        :param outdir: Output directory to extract data into
        :param remove_outdir: If True `outdir` will be forcefully removed before extracting. (Default: False)
        :param convert_cryxml_fmt: Format to automatically convert CryXml binary data to during extraction.
            (Default: 'xml')
        :param skip_lods: Skip exporting/processing `_lod` files. (Default: True)
        :param auto_unsplit_textures: If True, will automatically combine `dds.N` files into a single texture
            (Default: False)
        :param auto_convert_textures: If True, `.dds` files will automatically be converted to `tif` files. This will
            forcefully enable `auto_unsplit_textures`. The original DDS file will also be extracted. (Default: False)
        :param report_tex_conversion_errors: By default, texture conversion errors will be silently ignored.
        :param convert_dds_fmt: The output format to convert DDS textures to. Default '.png'
        :param extract_sounds: If True, discover sound files are extracted and converted. The output files will contain
            the trigger name associated with the sound, and the wem_id of the sound file. There may be multiple sounds
            associated with each trigger name. (Default: True)
        :param auto_convert_models: If True, `cgf-converter` will be run on each extracted model file. (Default: False)
        :param cgf_converter_opts: Override the default flags passed to cgf_converter during model conversion.
        :param auto_convert_sounds: If True, `ww2ogg` and `revorb` will be run on each extracted wem. (Default: False)
        :param ww2ogg: Override which `ww2ogg` binary used for audio conversion. Will be auto-discovered by default.
        :param revorb: Override which `revorb` binary used for audio conversion. Will be auto-discovered by default.
        :param cgf_converter: Override which `cgf-converter` binary used for model conversion.
            Will be auto-discovered by default.
        :param exclude: List of files to exclude from extraction. For example, if they're known to have already been
            extracted. This can speed up processing or avoid re-processing files.
        :param monitor: Callable function to output status messages to. Defaults to `monitor`
        :return: List of extracted files.
        """
        # Reset everything just in case
        self._reset()

        self.monitor = monitor
        self.convert_cryxml_fmt = convert_cryxml_fmt
        self.skip_lods = skip_lods

        self.outdir = Path(outdir)
        if remove_outdir and self.outdir.is_dir():
            self.log(f'Removing old output dir: {self.outdir}')
            shutil.rmtree(self.outdir)

        # setup extract dir
        self.log(f'Output dir: {self.outdir}')
        self.outdir.mkdir(parents=True, exist_ok=True)

        self.log(f'Extracting {self.entity.name} ({self.entity.guid}) from {self.sc.version_label}\n' + '-' * 80)

        # write out the record itself
        with (self.outdir / f'{self.entity.name}.json').open('w') as j:
            j.write(self.entity.to_json())

        ################################################################################################################
        # region process datacore records
        tints_dir = self.outdir / 'tint_palettes' / self.entity.name
        tints_dir.mkdir(parents=True, exist_ok=True)

        self._add_record_to_extract(self.entity.guid)
        processed_records = set()
        while self._cache['records_to_process']:
            cur_records_to_process = self._cache['records_to_process'] - processed_records
            self._cache['records_to_process'] = set()
            for record in cur_records_to_process:
                self._process_record(record)  # processed records could add more records to process
            processed_records |= cur_records_to_process
        # endregion process datacore records
        ################################################################################################################

        ################################################################################################################
        # region process files
        processed_files = set()
        while self._cache['files_to_process']:
            cur_files_to_process = self._cache['files_to_process'] - processed_files
            self._cache['files_to_process'] = set()
            for path in cur_files_to_process:
                self._process_p4k_file(path)  # processed files could add more files to process
            processed_files |= cur_files_to_process
        # endregion process files
        ################################################################################################################

        ################################################################################################################
        # region generate blueprint
        with (self.outdir / f'{self.entity.name}.scbp').open('w') as bpfile:
            bp = {
                'name': self.entity.name,
                'item_ports': {},
                'bone_names': self._cache['bone_names'],
                'geometry': self._cache['found_geometry'],
            }
            for port, ents in self._cache['item_ports'].items():
                for ent in ents:
                    if ent in self._cache['record_geometry']:
                        for tag in self._cache['record_geometry'][ent]:
                            if tag and tag in port:
                                bp['item_ports'].setdefault(port, set()).update(
                                    self._cache['record_geometry'][ent][tag])
                                break
                        else:
                            bp['item_ports'].setdefault(port, set()).update(
                                self._cache['record_geometry'][ent].get('', []))
                    elif ent in self._cache['found_geometry']:
                        bp['item_ports'].setdefault(port, set()).add(ent)
            json.dump(bp, bpfile, indent=2, cls=SCJSONEncoder)
        # endregion generate blueprint
        ################################################################################################################

        ################################################################################################################
        # region write all files to disk
        try:
            self.log('\n\nExtracting files\n' + '-' * 80)
            if exclude:
                self._cache['exclude'].update(exclude)
            self._cache['files_to_extract'] = self.sc.p4k.search(self._cache['files_to_extract'], ignore_case=True,
                                                                 mode='in_strip', exclude=self._cache['exclude'])
            self.sc.p4k.extractall(outdir, self._cache['files_to_extract'], convert_cryxml=True,
                                   convert_cryxml_fmt=convert_cryxml_fmt, monitor=self.log)
            extracted_files = [_.filename for _ in self._cache['files_to_extract']]
        except Exception as e:
            self.log(f'error extracting files {e}', logging.ERROR)
            return []
        # endregion write all files to disk
        ################################################################################################################

        ################################################################################################################
        # region process textures
        if auto_convert_textures or auto_unsplit_textures:
            self.log('\n\nUn-splitting textures\n' + '-' * 80)
            found_textures = set()
            for dds_file in [_ for _ in extracted_files if '.dds.' in _.lower()]:
                _ = Path(dds_file)
                if is_glossmap(dds_file):
                    found_textures.add(outdir / _.parent / f'{_.name.split(".")[0]}.dds.a')
                else:
                    found_textures.add(outdir / _.parent / f'{_.name.split(".")[0]}.dds')

            def _do_unsplit(dds_file):
                msgs = []
                try:
                    outfile = collect_and_unsplit(Path(dds_file), outfile=Path(dds_file), remove=True)
                    msgs.append((f'un-split {outfile.relative_to(outdir)}', logging.INFO))
                except Exception as e:
                    return [(f'failed to un-split {dds_file}: {repr(e)}', logging.ERROR)]

                try:
                    if auto_convert_textures and all(_ not in outfile.name.lower() for _ in TEXCONV_IGNORE):
                        tex_convert(infile=outfile, outfile=outfile.with_suffix(f'.{convert_dds_fmt}'))
                        msgs.append((f'converted {outfile.relative_to(outdir)} to {convert_dds_fmt}', logging.INFO))
                except Exception as e:
                    if report_tex_conversion_errors:
                        return [(f'failed to convert {dds_file}: {repr(e)}', logging.ERROR)]
                return msgs

            with concurrent.futures.ThreadPoolExecutor() as executor:
                futures = [executor.submit(_do_unsplit, dds_file=_) for _ in found_textures]
                for future in concurrent.futures.as_completed(futures):
                    for msg in future.result():
                        self.log(*msg)
        # endregion process textures
        ################################################################################################################

        ################################################################################################################
        # region convert models
        cgf_converter = cgf_converter or CGF_CONVERTER
        if auto_convert_textures and not cgf_converter:
            self.log(
                '\n\ncould not determine location of cgf-converter. Please ensure it can be found in system '
                'the path\n', logging.ERROR)
        elif auto_convert_models:
            def _do_model_convert(model_file):
                cgf_cmd = f'"{cgf_converter}" {cgf_converter_opts} "{model_file}" -objectdir "{obj_dir}"'
                cgf = subprocess.Popen(cgf_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

                start_time = time.time()
                while (time.time() - start_time) < CGF_CONVERTER_TIMEOUT:
                    if cgf.poll() is not None:
                        break
                    time.sleep(1)
                else:
                    # timed out, kill the process
                    cgf.terminate()
                if cgf.returncode != 0:
                    errmsg = cgf.stdout.read().decode('utf-8')
                    if 'is being used by another process' in errmsg.lower():
                        return []  # someone else already picked up this file, ignore the error
                    return [(f'model conversion failed for {model_file}: \n{errmsg}\n\n', logging.ERROR)]
                return [(f'converted {model_file}', logging.INFO)]

            self.log('\n\nConverting Models\n' + '-' * 80)
            obj_dir = outdir / 'Data'
            with concurrent.futures.ThreadPoolExecutor() as executor:
                futures = []
                for model_file in [_ for _ in extracted_files if
                                   '.' + _.split('.')[-1].lower() in CGF_CONVERTER_MODEL_EXTS]:
                    model_file = outdir / Path(model_file)
                    futures.append(executor.submit(_do_model_convert, model_file=model_file))
                for future in concurrent.futures.as_completed(futures):
                    for msg in future.result():
                        self.log(*msg)
        # endregion convert models
        ################################################################################################################

        self.log(f'finished extracting {self.entity.name}')
        return extracted_files


def extract_ship(sc_or_scdir, ship_guid_or_path: typing.Union[str, Path], outdir: typing.Union[str, Path],
                 remove_outdir: bool = False, monitor: typing.Callable = print, **kwargs) -> typing.List[str]:
    """
    Process and extract a `ShipEntity` records (typically found in `entities/spaceships`) which contain all the
    information pertaining to a ship in game, the models, components, object containers, etc. This utility will
    recursively parse this record, and all referenced objects within it to find and extract all data pertaining to the
    ship.

    See :class:EntityExtractor.extract for all parameters

    :param sc_or_scdir: :class:`StarCitizen` or Star Citizen installation directory (containing Data.p4k)
    :param ship_guid_or_path: The GUID or DataCore path of the `ShipEntity` to extract
    :param outdir: Output directory to extract data into
    :return: List of extracted files.
    """
    from scdatatools.sc import StarCitizen
    if isinstance(sc_or_scdir, StarCitizen):
        sc = sc_or_scdir
    else:
        sc = StarCitizen(sc_or_scdir)

    monitor(f'Opening {sc.version_label}...')
    sc.load_all()
    sc.wwise.load_all_game_files()

    if str(ship_guid_or_path) in sc.datacore.records_by_guid:
        ship = dco_from_guid(sc.datacore, ship_guid_or_path)
    else:
        ships = sc.datacore.search_filename(f'{ship_guid_or_path}.xml', mode='endswith')
        if not ships:
            ships = sc.datacore.search_filename(ship_guid_or_path)
        if not ships or len(ships) > 1:
            raise ValueError(f'Could not determine which ship entity to extract from "{ship_guid_or_path}"')
        ship = dco_from_guid(sc.datacore, ships[0].id)

    extractor = EntityExtractor(sc, ship)
    return extractor.extract(outdir=outdir, remove_outdir=remove_outdir, monitor=monitor, **kwargs)
