import re
import json
import time
import shutil
import typing
import logging
import traceback
import subprocess
from pathlib import Path
import concurrent.futures
from xml.etree import ElementTree
from contextlib import contextmanager

from pyquaternion import Quaternion

from scdatatools.cry.model.ivo import Ivo
from scdatatools.cry.model.chcr import ChCr
from scdatatools.utils import SCJSONEncoder
from scdatatools.forge.dftypes import Record, GUID
from scdatatools.cry.model import chunks as ChCrChunks
from scdatatools.forge.dco import dco_from_guid, DataCoreObject
from scdatatools.sc.textures import tex_convert, collect_and_unsplit, is_glossmap, ConverterUtility
from scdatatools.utils import etree_to_dict, norm_path, dict_search
from scdatatools.cry.model.utils import Vector3D
from scdatatools.cry.cryxml import dict_from_cryxml_file, dict_from_cryxml_string, CryXmlConversionFormat

logger = logging.getLogger(__name__)

PROCESS_FILES = [
    'mtl', 'chrparams', 'cga', 'cgam', 'cgf', 'cgfm', 'soc', 'xml', 'entxml', 'chr', 'rmp', 'dba', 'animevents',
    'skin', 'skinm', 'cdf'
]
CGF_CONVERTER_MODEL_EXTS = ['.cga', '.cgf', '.chr', '.skin']
CGF_CONVERTER_TIMEOUT = 5 * 60  # assume cgf converter is stuck after this much time
CGF_CONVERTER_DEFAULT_OPTS = '-en "$physics_proxy" -em proxy -em nocollision_faces -prefixmatnames -group -smooth -notex'
RECORDS_BASE_PATH = Path('libs/foundry/records/')
SHIP_ENTITIES_PATH = RECORDS_BASE_PATH / 'entities/spaceships'
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
SOC_ENTITY_CLASSES_TO_SKIP = [
    # TODO: all TBDs in here are entityclasses in soc cryxmlbs that havent been researched yet
    "AreaBox",  # TODO: TBD
    "AreaShape",  # TODO: TBD
    "AudioAreaAmbience",  # TODO: TBD
    "AudioEnvironmentFeedbackPoint",  # TODO: TBD
    "AudioTriggerSpot",  # TODO: TBD
    "CameraSource",  # TODO: TBD
    "EditorCamera",  # TODO: TBD
    "EnvironmentLight",  # TODO: TBD
    "FogVolume",  # TODO: TBD
    "GravityBox",  # TODO: TBD
    "Hazard",  # TODO: TBD
    "LandingArea",  # TODO: TBD
    "LedgeObject",  # TODO: TBD
    "Light",  # TODO: TBD
    "LightBox",  # TODO: TBD
    "LightGroup",  # TODO: TBD
    "LightGroupPoweredItem",  # TODO: TBD
    "NavigationArea",  # TODO: TBD
    "ParticleField",  # TODO: TBD
    "ParticleEffect",  # TODO: TBD
    "Room",  # Audio # TODO: TBD
    "RoomConnector",  # TODO: TBD
    "RotationSimple",  # TODO: TBD
    'SequenceObjectItem',  # TODO: TBD
    "SurfaceRaindropsTarget",  # TODO: TBD
    "TagPoint",  # TODO: TBD
    'TransitDestination',  # TODO: TBD
    'TransitGateway',  # TODO: TBD
    'TransitManager',  # TODO: TBD
    'TransitNavSpline',  # TODO: TBD
    "VibrationAudioPoint",  # TODO: TBD
    "VehicleAudioPoint",  # TODO: TBD
]


class Geometry(dict):
    def __init__(self, name, geom_file, pos=None, rotation=None, scale=None, materials=None, attrs=None,
                 helpers=None, parent=None):
        super().__init__()
        self['name'] = name
        self['geom_file'] = geom_file
        self['instances'] = {}
        self['loadout'] = {}
        self['materials'] = set()
        self['sub_geometry'] = {}
        self['helpers'] = helpers or {}
        self.add_materials(materials or [])
        if pos:
            self.add_instance('', pos, rotation, scale)
        self['attrs'] = attrs or {}
        self.parent = parent

    def add_materials(self, mats):
        # ensure material files have the correct suffix
        if not isinstance(mats, (list, tuple, set)):
            mats = [mats]
        self['materials'].update(Path(mat).with_suffix('.mtl').as_posix().lower() for mat in mats if mat)

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

    def add_sub_geometry(self, child_geom, pos=None, rotation=None, attrs=None):
        create_params = {
            "pos": pos or {"x": 0.0, "y": 0.0, "z": 0.0},
            "rotation": rotation or {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            "attrs": attrs or {}
        }
        if bone_name := create_params['attrs'].get('bone_name', ''):
            helper = self['helpers'].get(bone_name.lower(), {})
            if helper:
                create_params['pos'] = helper['pos']
                create_params['rotation'] = helper['rotation']
                create_params['attrs']['bone_name'] = helper['name']
        self['sub_geometry'].setdefault(child_geom['name'], []).append(create_params)

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
        self._current_container = ''
        self._cache = {
            'files_to_extract': set(),
            'files_to_process': set(),
            'found_records': set(),
            'audio_to_extract': set(),
            'records_to_process': set(),
            'bone_names': set(),
            'exclude': set(),
            'geometry': {},
            'found_geometry': {},
            'record_geometry': {},
            'containers': {},
        }

    @contextmanager
    def container(self, container):
        previous_container = self._current_container
        self._current_container = previous_container.setdefault(container, {
            'geometry': {},
        })
        yield
        self._current_container = previous_container

    def __init__(self, sc, entity: typing.Union[DataCoreObject, Record]):
        """
        Process and extract a `ShipEntity` records (typically found in `entities/spaceships`) which contain all the
        information pertaining to a ship in game, the models, components, object containers, etc. This utility will
        recursively parse this record, and all referenced objects within it to find and extract all data pertaining to
        the ship.

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
        self._current_container = ''
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
                if base.endswith('disp'):
                    # a lot of textures miss the 'l' at the end of the file... may as well catch them
                    base += 'l'
                    if base not in self._p4k_files:
                        self.log(f'could not find file in P4K: {path}', logging.WARNING)
                        return
                else:
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

    def _add_record_to_extract(self, guid: typing.Union[str, list, tuple, set, GUID]):
        if not guid:
            return
        if isinstance(guid, (list, tuple, set)):
            for g in guid:
                self._add_record_to_extract(g)
            return

        guid = str(guid)

        if guid not in self.sc.datacore.records_by_guid:
            return self.log(f'record {guid} does not exist', logging.WARNING)

        if guid not in self._cache['found_records']:
            record = self.sc.datacore.records_by_guid[guid]
            self.log(f'+ record: {Path(record.filename).relative_to(RECORDS_BASE_PATH).as_posix()}')
            self._cache['found_records'].add(guid)
            self._cache['records_to_process'].add(guid)
            outrec = (self.outdir / 'Data' / record.filename).with_suffix(f'.{self.convert_cryxml_fmt}')
            outrec.parent.mkdir(exist_ok=True, parents=True)
            with outrec.open('w') as out:
                if self.convert_cryxml_fmt == 'xml':
                    out.write(record.dcb.dump_record_xml(record))
                else:
                    out.write(record.dcb.dump_record_json(record))

    def _add_audio_to_extract(self, trigger_name):
        if trigger_name in self.sc.wwise.triggers:
            self._cache['audio_to_extract'].add(trigger_name)

    def _handle_ext_geom(self, rec, obj, tags='', helpers=None):
        helpers = helpers or {}
        if obj.name == 'SGeometryDataParams':
            mtl = obj.properties['Material'].properties['path']
            geom_path = obj.properties['Geometry'].properties['path']
            self._add_file_to_extract(mtl)

            if geom_path:
                p = None
                tints_dir = self.outdir / 'tint_palettes' / self.entity.name
                try:
                    tint_id = str(obj.properties['Palette'].properties['RootRecord'])
                    if tint_id != '00000000-0000-0000-0000-000000000000':
                        palette = self.sc.datacore.records_by_guid[tint_id]
                        p = tints_dir / f'{Path(geom_path).stem}_{palette.name}.json'
                        if p.is_file():
                            self.log(f'tint palette already exists: {p}', logging.ERROR)
                        else:
                            with p.open('w') as f:
                                f.write(self.sc.datacore.dump_record_json(palette))
                        self._add_file_to_extract(palette.properties['root'].properties['decalTexture'])
                except Exception as e:
                    traceback.print_exc()
                    self.log(f'could not dump tint: {e}', logging.WARNING)

                attrs = {'tags': tags}
                if p is not None:
                    attrs['palette'] = p.as_posix()
                geom, created = self._get_or_create_geom(geom_path, create_params={
                    'attrs': attrs, 'pos': Vector3D() if rec.guid == self.entity.guid else None,
                    'materials': mtl, 'helpers': helpers,
                })
                self._cache['record_geometry'].setdefault(rec.guid, {}).setdefault(tags, set()).add(geom['name'])

        if 'Geometry' in obj.properties:
            self._handle_ext_geom(rec, obj.properties['Geometry'], obj.properties.get('Tags', ''), helpers)
        if 'SubGeometry' in obj.properties:
            for sg in obj.properties.get('SubGeometry', []):
                self._handle_ext_geom(rec, sg, obj.properties.get('Tags', ''), helpers)
        if 'Material' in obj.properties:
            self._handle_ext_geom(rec, obj.properties['Material'], helpers)
        if 'path' in obj.properties:
            self._add_file_to_extract(obj.properties['path'])

    def geometry_for_record(self, record, base=False):
        if record is None:
            return None
        if isinstance(record, DataCoreObject):
            guid = record.guid
        elif isinstance(record, Record):
            guid = record.id.value
        else:
            guid = record
        self._add_record_to_extract(guid)  # make sure the record has been tracked at least at some point
        if guid in self._cache['records_to_process']:
            self._cache['records_to_process'].remove(guid)
            self._cache['found_records'].add(guid)
            self._process_record(guid)
        geom = self._cache['record_geometry'].get(guid, {})
        if base and geom:
            return next(iter(geom['']))
        return geom

    def _get_or_create_item_port(self, name, parent) -> dict:
        if name not in parent:
            parent[name] = {'geometry': set()}
        return parent[name]

    def _handle_component_loadouts(self, rec, obj, parent=None, helpers=None):
        helpers = helpers or {}
        try:
            for entry in obj.properties['loadout'].properties.get('entries', []):
                try:
                    if not entry.properties['entityClassName']:
                        continue

                    if entry.properties['entityClassName'] not in self._records_by_name:
                        self.log(f'Could not find record "{entry.properties["entityClassName"]}', logging.WARNING)
                        continue

                    ipe = self._records_by_name[entry.properties["entityClassName"]]
                    self._add_record_to_extract(ipe.id)
                    port_name = entry.properties['itemPortName']

                    def _geom_for_port(port):
                        ipe_geom = self.geometry_for_record(ipe)
                        for tag in ipe_geom:
                            if tag and tag in port:
                                return ipe_geom[tag]
                        return ipe_geom.get('', [])

                    if port_name in helpers:
                        helper = helpers[port_name]
                        parent_geom = self.geometry_for_record(rec, base=True)
                        parent_geom, _ = self._get_or_create_geom(parent_geom)
                        for geom_path in _geom_for_port(helper['name']):
                            self._get_or_create_geom(geom_path, parent=parent_geom, create_params={
                                'pos': helper['pos'], 'rotation': helper['rotation'],
                                'attrs': {'bone_name': helper['name'].lower()}
                            })
                        self._cache['bone_names'].add(helper['name'].lower())
                    else:
                        # assign record to be instanced at the set itemPortName
                        port_name = port_name.lower()
                        ip = self._get_or_create_item_port(port_name, parent=parent)
                        ip['geometry'].update(_geom_for_port(port_name))
                        self._cache['bone_names'].add(port_name)
                        if entry.properties['loadout']:
                            self._handle_component_loadouts(rec, entry,
                                                            parent=ip.setdefault('loadout', {}),
                                                            helpers=helpers)
                except Exception as e:
                    traceback.print_exc()
                    self.log(f'processing component SEntityComponentDefaultLoadoutParams: {repr(e)}', logging.ERROR)
        except Exception as e:
            traceback.print_exc()
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
                d = chunk.dict()
                # Root can be Entities or SCOC_Entities
                entities = d.get('Entities', d.get('SCOC_Entities', {})).get('Entity')
                if isinstance(entities, dict):
                    entities = [entities]  # only one entity in this cryxmlb
                for entity in entities:
                    try:
                        geom = None
                        if 'EntityGeometryResource' in entity.get('PropertiesDataCore', {}):
                            geom, _ = self._get_or_create_geom(
                                entity['PropertiesDataCore']['EntityGeometryResource']
                                ['Geometry']['Geometry']['Geometry']['@path']
                            )
                        elif entity.get('@EntityClass') in SOC_ENTITY_CLASSES_TO_SKIP:
                            continue  # TODO: handle these, see SOC_ENTITY_CLASSES_TO_SKIP
                        elif ecguid := entity.get('@EntityClassGUID'):
                            geom = self.geometry_for_record(self.sc.datacore.records_by_guid.get(ecguid), base=True)
                            geom, _ = self._get_or_create_geom(geom)
                        if geom is not None:
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
                        else:
                            self.log(f'WARNING: non-skipped soc EntityClass doesnt have geometry: '
                                     f'{entity.get("@EntityClass")}', logging.WARNING)
                    except Exception as e:
                        traceback.print_exc()
                        self.log(f'Failed to parse soc cryxmlb entity "{entity["@Name"]}": {repr(e)}')

    def _handle_vehicle_definition(self, rec, def_p4k_path):
        def_p4k_path = norm_path(f'{"" if def_p4k_path.startswith("data") else "data/"}{def_p4k_path}').lower()
        self._add_file_to_extract(def_p4k_path)
        if def_p4k_path in self._cache['files_to_process']:
            self._cache['files_to_process'].remove(def_p4k_path)

        vdef_info = self.sc.p4k.NameToInfoLower[def_p4k_path]
        vdef = dict_from_cryxml_file(self.sc.p4k.open(vdef_info))

        def _walk_parts(part):
            if isinstance(part.get('Part'), dict):
                yield from _walk_parts(part['Part'])
            elif 'Part' in part:
                for p in part['Part']:
                    yield from _walk_parts(p)
            elif 'Parts' in part:
                yield from _walk_parts(part['Parts'])
            else:
                yield part
            yield part

        parent_geom = self.geometry_for_record(rec, base=True)
        parent_geom, _ = self._get_or_create_geom(parent_geom)
        parts = {}
        parent_parts = {}
        for part in _walk_parts(vdef['Vehicle']['Parts']):
            if part.get('@class', '') == 'Tread':
                parent_parts[part['@name'].lower()] = {
                    'filename': part['Tread']['@filename'],
                    'children': [
                        _['@partName'] for _ in part['Tread']['Wheels']['Wheel']
                    ]
                }
                self._cache['bone_names'].add(part['Tread']['Sprocket']['@name'].lower())
                self._add_material(part['Tread'].get('@materialName', ''))
            if 'SubPart' in part and part.get('@class', '') == 'SubPartWheel':
                parts[part['@name'].lower()] = part['SubPart']['@filename']

        for parent_part_name, params in parent_parts.items():
            parent_part_geom, _ = self._get_or_create_geom(params['filename'])
            ip = self._get_or_create_item_port(parent_part_name, parent=parent_geom['loadout'])
            ip['geometry'].add(parent_part_geom['name'])
            self._cache['bone_names'].add(parent_part_name)
            for child_part_name in params['children']:
                if child_part_name not in parts:
                    self.log(f'did not find child part {child_part_name} for {parent_part_name} in '
                             f'{parent_geom["name"]}')
                    continue
                child_part = parts.pop(child_part_name)
                child_geom, _ = self._get_or_create_geom(child_part)
                ip = self._get_or_create_item_port(child_part_name, parent=parent_part_geom['loadout'])
                ip['geometry'].add(child_geom['name'])
                self._cache['bone_names'].add(child_part_name)

        for part, part_file in parts.items():
            geom, _ = self._get_or_create_geom(part_file)
            ip = self._get_or_create_item_port(part, parent=parent_geom['loadout'])
            ip['geometry'].add(geom['name'])
            self._cache['bone_names'].add(part)

    def _handle_vehicle_components(self, rec, vc, helpers=None):
        helpers = helpers or {}
        for prop in ['landingSystem']:
            if vc.properties.get(prop):
                self._add_record_to_extract(vc.properties[prop])
        for prop in ['physicsGrid']:
            if prop in vc.properties:
                self._search_record(vc.properties[prop])
        if vc.properties.get('vehicleDefinition'):
            self._handle_vehicle_definition(rec, vc.properties['vehicleDefinition'])
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
                    soc = self.sc.p4k.NameToInfoLower.get(f'data/{soc_path.as_posix()}'.lower())
                    if soc is not None:
                        soc = ChCr(soc.open().read())
                        self._cache['bone_names'].add(oc.properties['boneName'].lower())
                        # TODO: pass/handle the offset pos/rotation that is along side the bone_name
                        self._handle_soc(oc.properties['boneName'], soc)
                except Exception as e:
                    traceback.print_exc()
                    self.log(f'failed to process object container "{p4k_path}": {repr(e)}', logging.ERROR)
                    raise

    def _handle_audio_component(self, rec, ac, helpers=None):
        helpers = helpers or {}
        print("TODO: 'ShipAudioComponentParams'")
        print("TODO: 'AudioPassByComponentParams'")
        # TODO: dict search component for audioTrigger?

    def _handle_landinggear(self, r, helpers=None):
        helpers = helpers or {}
        parent_geom = self.geometry_for_record(self.entity, base=True)
        parent_geom, _ = self._get_or_create_geom(parent_geom)
        for gear in r.record.properties['gears']:
            self._handle_ext_geom(r, gear.properties['geometry'])
            geom, _ = self._get_or_create_geom(gear.properties['geometry'].properties['path'])
            ip = self._get_or_create_item_port(gear.properties['bone'], parent=parent_geom['loadout'])
            ip['geometry'].add(geom['name'])
            self._cache['bone_names'].add(gear.properties['bone'].lower())

    def _search_record(self, r):
        """ This is a brute-force method of extracting related files from a datacore record. It does no additional
        processing of the record, if there is specific data that should be extracted a different method should be
        implemented and used for that record type. """
        d = self.sc.datacore.record_to_dict(r)
        self._add_file_to_extract(dict_search(d, RECORD_KEYS_WITH_PATHS, ignore_case=True))

    def _search_record_audio(self, r, component):
        d = self.sc.datacore.record_to_dict(r)
        self._add_audio_to_extract(dict_search(d, RECORD_KEYS_WITH_AUDIO, ignore_case=True))

    def _process_record(self, r):
        r = dco_from_guid(self.sc.datacore, r)
        if r.type == 'EntityClassDefinition':
            helpers = {}
            if 'SItemPortContainerComponentParams' in r.components:
                helper_ports = r.components['SItemPortContainerComponentParams'].properties['Ports']
                for port in helper_ports:
                    try:
                        helper = port.properties['AttachmentImplementation'].properties['Helper'].properties['Helper']
                    except KeyError:
                        continue
                    offset = helper.properties['Offset']
                    helpers[port.properties['Name'].lower()] = {
                        'pos': Vector3D(**offset.properties['Position'].properties),
                        'rotation': Quaternion(w=1, **offset.properties['Rotation'].properties),
                        'name': helper.properties['Name'].lower()
                    }
                    self._cache['bone_names'].add(helper.properties['Name'].lower())

            if 'SGeometryResourceParams' in r.components:
                self._handle_ext_geom(r, r.components['SGeometryResourceParams'], helpers=helpers)
            if 'SEntityComponentDefaultLoadoutParams' in r.components:
                geom_path = self.geometry_for_record(r, base=True)
                geom, _ = self._get_or_create_geom(geom_path)
                self._handle_component_loadouts(r, r.components['SEntityComponentDefaultLoadoutParams'],
                                                helpers=helpers, parent=geom['loadout'])
            if 'VehicleComponentParams' in r.components:
                self._handle_vehicle_components(r, r.components['VehicleComponentParams'], helpers=helpers)
            if 'ShipAudioComponentParams' in r.components:
                self._handle_audio_component(r, r.components['ShipAudioComponentParams'], helpers=helpers)
            if 'AudioPassByComponentParams' in r.components:
                self._handle_audio_component(r, r.components['AudioPassByComponentParams'], helpers=helpers)

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

    def _get_or_create_geom(self, geom_path, parent=None, create_params=None, sub_geometry=None) -> (Geometry, bool):
        if not geom_path:
            return None, False

        created = False
        sub_geometry = sub_geometry or {}
        if not isinstance(geom_path, Path):
            geom_path = Path(geom_path)

        self._add_file_to_extract(geom_path)

        if geom_path.suffix.lower() == '.cdf':
            # parse the cdf and create it's sub_geometry as well
            try:
                p4k_path = (Path('data') / geom_path) if geom_path.parts[0].lower() != 'data' else geom_path
                p4k_info = self.sc.p4k.NameToInfoLower[p4k_path.as_posix().lower()]
                cdf = dict_from_cryxml_file(self.sc.p4k.open(p4k_info))['CharacterDefinition']
                geom_path = Path(cdf['Model']['@File'])
                sub_geometry.update({
                    _['@Binding']: {'attrs': {'bone_name': _['@AName']}}
                    for _ in cdf['AttachmentList'].values()
                })
            except KeyError:
                self.log(f'failed to parse cdf: {geom_path}', logging.ERROR)
                return None, False

        if geom_path.suffix.lower() == '.cgf':
            # check to see if there is a cga equivalent, and use that instead
            test_path = (Path('data') / geom_path) if geom_path.parts[0].lower() != 'data' else geom_path
            if test_path.with_suffix('.cga').as_posix().lower() in self.sc.p4k.NameToInfoLower:
                geom_path = geom_path.with_suffix('.cga')

        geom_name = geom_path.as_posix().lower()
        if geom_path.parts[0].lower() == 'data':
            geom_name = geom_name[5:]
            geom_path = Path(*geom_path.parts[1:])

        if parent is not None:
            child_geom, _ = self._get_or_create_geom(geom_path, create_params=create_params)
            parent.add_sub_geometry(child_geom, **create_params)
            return parent, True

        if geom_name not in self._cache['found_geometry']:
            self._cache['found_geometry'][geom_name] = Geometry(name=geom_name, geom_file=geom_path,
                                                                **(create_params or {}))
            for sub_geo, sub_params in sub_geometry.items():
                self._get_or_create_geom(sub_geo, self._cache['found_geometry'][geom_name], sub_params)
            created = True

        return self._cache['found_geometry'][geom_name], created

    def _add_material(self, path, model_path=''):
        if not path:
            return ''
        mat = Path(path)
        if mat.parent.parent == mat.parent and model_path:
            # material is a path local to the model
            mat = Path(model_path).parent / mat
            if mat.with_suffix('').as_posix().lower() not in self._p4k_files:
                # material is a path in the `textures` directory next to the model?
                mat = (Path(model_path).parent / 'textures' / mat)
                if mat.with_suffix('').as_posix().lower() not in self._p4k_files:
                    self.log(f'Could not find path for material "{path}')
                    return ''
        mat = mat.with_suffix('.mtl')
        self._add_file_to_extract(mat)
        return mat

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
                        for mat in dict_search(x, '@material', ignore_case=True):
                            self._add_material(mat)

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
                        geom, _ = self._get_or_create_geom(path)
                        geom.add_materials(self._add_material(mtl_path, path))
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
            traceback.print_exc()
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
                ww2ogg: str = '', revorb: str = '', cgf_converter: str = '', tex_converter: str = '',
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
                'bone_names': sorted(self._cache['bone_names']),
                'geometry': self._cache['found_geometry'],
            }
            json.dump(bp, bpfile, indent=2, cls=SCJSONEncoder)
            print(f'created blueprint {bpfile.name}')
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
            self.sc.p4k.extractall(self.outdir, self._cache['files_to_extract'], convert_cryxml=True,
                                   convert_cryxml_fmt=convert_cryxml_fmt, monitor=self.log)
            extracted_files = [_.filename for _ in self._cache['files_to_extract']]
        except Exception as e:
            traceback.print_exc()
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
                    found_textures.add(self.outdir / _.parent / f'{_.name.split(".")[0]}.dds.a')
                else:
                    found_textures.add(self.outdir / _.parent / f'{_.name.split(".")[0]}.dds')

            converter = ConverterUtility(
                'texconv' if 'texconv' in Path(tex_converter).name.lower() else 'compressonatorcli'
            )

            def _do_unsplit(dds_file):
                msgs = []
                try:
                    unsplit = collect_and_unsplit(Path(dds_file), outfile=Path(dds_file), remove=True)
                    msgs.append((f'un-split {unsplit.relative_to(self.outdir)}', logging.INFO))
                except Exception as e:
                    traceback.print_exc()
                    return [(f'failed to un-split {dds_file}: {repr(e)}', logging.ERROR)]

                try:
                    if auto_convert_textures:
                        if is_glossmap(unsplit):
                            outfile = unsplit.with_name(f'{unsplit.name.split(".")[0]}.glossmap.{convert_dds_fmt}')
                        else:
                            outfile = unsplit.with_suffix(f'.{convert_dds_fmt}')
                        tex_convert(infile=unsplit, outfile=outfile, converter=converter, converter_bin=tex_converter)
                        msgs.append(
                            (f'converted {unsplit.relative_to(self.outdir)} to {convert_dds_fmt}', logging.INFO))
                except Exception as e:
                    traceback.print_exc()
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
            obj_dir = self.outdir / 'Data'
            with concurrent.futures.ThreadPoolExecutor() as executor:
                futures = []
                for model_file in [_ for _ in extracted_files if
                                   '.' + _.split('.')[-1].lower() in CGF_CONVERTER_MODEL_EXTS]:
                    model_file = self.outdir / Path(model_file)
                    if model_file.suffix == '.cgf' and model_file.with_suffix('.cga').is_file():
                        continue  # skip converting cgf files if the cga equivalent is available
                    futures.append(executor.submit(_do_model_convert, model_file=model_file))
                for future in concurrent.futures.as_completed(futures):
                    for msg in future.result():
                        self.log(*msg)
        # endregion convert models
        ################################################################################################################

        self.log(f'finished extracting {self.entity.name}')
        return extracted_files


def extract_entity(sc_or_scdir, entity_guid_or_path: typing.Union[str, Path], outdir: typing.Union[str, Path],
                   remove_outdir: bool = False, monitor: typing.Callable = print, **kwargs) -> typing.List[str]:
    """
    Process and extract `Entity` records (typically found in `entities/`) which contain all the
    information pertaining to a ship/vehicle/etc. in game, the models, components, object containers, etc. This utility 
    will recursively parse this record, and all referenced objects within it to find and extract all data pertaining to 
    the entity.

    See :class:EntityExtractor.extract for all parameters

    :param sc_or_scdir: :class:`StarCitizen` or Star Citizen installation directory (containing Data.p4k)
    :param entity_guid_or_path: The GUID or DataCore path of the `ShipEntity` to extract
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
    # sc.wwise.load_all_game_files()

    if str(entity_guid_or_path) in sc.datacore.records_by_guid:
        ship = dco_from_guid(sc.datacore, entity_guid_or_path)
    else:
        ships = sc.datacore.search_filename(f'{entity_guid_or_path}.xml', mode='endswith')
        if not ships:
            ships = sc.datacore.search_filename(entity_guid_or_path)
        if not ships or len(ships) > 1:
            raise ValueError(f'Could not determine which ship entity to extract from "{entity_guid_or_path}"')
        ship = dco_from_guid(sc.datacore, ships[0].id)

    extractor = EntityExtractor(sc, ship)
    return extractor.extract(outdir=outdir, remove_outdir=remove_outdir, monitor=monitor, **kwargs)
