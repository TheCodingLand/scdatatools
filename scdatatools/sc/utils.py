import json
import shutil
import typing
from pathlib import Path
from xml.etree import ElementTree

from scdatatools.sc import StarCitizen
from scdatatools.cry.model import chunks as ChCrChunks
from scdatatools.cry.model.chcr import ChCr
from scdatatools.cry.model.ivo import Ivo
from scdatatools.forge.dco import dco_from_guid
from scdatatools.cry.cryxml import dict_from_cryxml_file, dict_from_cryxml_string
from scdatatools.utils import etree_to_dict, norm_path, dict_search

PROCESS_FILES = [
    'mtl', 'chrparams', 'cga', 'cgam', 'cgf', 'cgfm', 'soc', 'xml', 'entxml', 'chr', 'rmp', 'dba', 'animevents',
    'skin', 'skinm', 'cdf'
]
SHIP_ENTITES_PATH = 'libs/foundry/records/entities/spaceships'


def extract_ship(sc_or_scdir, ship_guid_or_path, outdir, remove_outdir=False, monitor=print):
    # Track every p4k file we need to extract
    files_to_extract = set()
    files_to_process = set()

    outdir = Path(outdir)
    if remove_outdir and outdir.is_dir():
        monitor(f'Removing old output dir: {outdir}')
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    if isinstance(sc_or_scdir, StarCitizen):
        sc = sc_or_scdir
    else:
        sc = StarCitizen(sc_or_scdir)
    monitor(f'Opening {sc.version_label}...')

    if str(ship_guid_or_path) in sc.datacore.records_by_guid:
        ship = dco_from_guid(sc, ship_guid_or_path)
    else:
        ships = sc.datacore.search_filename(f'{SHIP_ENTITES_PATH}/*{ship_guid_or_path}.xml')
        if not ships:
            ships = sc.datacore.search_filename(ship_guid_or_path)
        if not ships or len(ships) > 1:
            raise ValueError(f'Could not determine which ship entity to extract from "{ship_guid_or_path}"')
        ship = dco_from_guid(sc, ships[0].id)

    monitor(f'Extracting {ship.name} ({ship.guid}) from {sc.version_label}\n' + '-' * 80)

    # create a convenience quick lookup for base filenames
    p4k_files = set(_.lower().split('.', maxsplit=1)[0] for _ in sc.p4k.namelist())

    # setup extract dir
    outdir = outdir / ship.name
    monitor(f'Output dir: {outdir}')
    outdir.mkdir(parents=True, exist_ok=True)

    # write out the record itself
    with (outdir / f'{ship.name}.json').open('w') as j:
        j.write(ship.to_json())

    def add_file_to_extract(path: typing.Union[str, list, tuple, set]):
        if not path:
            return
        if isinstance(path, (list, tuple, set)):
            for p in path:
                add_file_to_extract(p)
            return
        path = path.lower()
        path = norm_path(f'{"" if path.startswith("data") else "data/"}{path}')
        if '.' not in path:
            # add whole dir
            if path not in files_to_extract:
                files_to_extract.add(path)
                monitor(f'+ dir ex: {path}')
        else:
            base, ext = path.split('.', maxsplit=1)
            if base not in p4k_files:
                monitor(f'WARN: Could not find file in P4K: {path}')
                return

            if base not in files_to_extract:
                files_to_extract.add(base)
                monitor(f'+ file ex: {base}')
            if ext in PROCESS_FILES:
                files_to_process.add(path)
            else:
                # second split handles things like .dds.1
                if ext.split('.')[0] not in ['dds', 'tif', 'socpak', 'brmp', 'obj']:
                    # TODO: figure out what BRMP files are
                    monitor(f'WARN: unhandled file {ext} {path}')
                    # TODO: add support for gfx files:
                    #      'data/ui/environmentalscreens/ships/idris/fluff/swf/9x16-small_securitycode.gfx'

    ##################################################################################################################
    # region Exterior geometry/tints/materials
    tints_dir = outdir / 'tint_palettes'
    tints_dir.mkdir()

    def handle_ext_geom(obj):
        if 'Geometry' in obj.properties:
            handle_ext_geom(obj.properties['Geometry'])
        if 'SubGeometry' in obj.properties:
            for sg in obj.properties.get('SubGeometry', []):
                handle_ext_geom(sg)
        if 'Material' in obj.properties:
            handle_ext_geom(obj.properties['Material'])

        if 'path' in obj.properties:
            add_file_to_extract(obj.properties['path'])
        if 'Palette' in obj.properties:
            try:
                tint_id = str(obj.properties['Palette'].properties['RootRecord'])
                if tint_id != '00000000-0000-0000-0000-000000000000':
                    p = sc.datacore.records_by_guid[tint_id]
                    with (tints_dir / f'{p.name}.json').open('w') as f:
                        f.write(sc.datacore.dump_record_json(p))
                    add_file_to_extract(p.properties['root'].properties['decalTexture'])
            except Exception as e:
                monitor(f'WARN: could not dump tint: {e}')

    handle_ext_geom(ship.components['SGeometryResourceParams'])
    # endregion Exterior geometry/tints/materials
    ##################################################################################################################

    ##################################################################################################################
    # region Object Containers
    for oc in ship.object_containers:
        try:
            p4k_path = norm_path(oc.properties["fileName"])
            add_file_to_extract(p4k_path)  # extract the socpak itself
            # extract all files inside the socpak
            archive = sc.p4k.NameToInfoLower[f'data/{p4k_path}'.lower()]
            add_file_to_extract([_.filename for _ in archive.filelist])
        except Exception as e:
            monitor(f'ERROR: Failed to process object container "{p4k_path}": {repr(e)}')

    # endregion Object Containers
    ##################################################################################################################

    ##################################################################################################################
    # region process files
    keys_with_paths = [
        '@File',   # mtl
        '@path',   # chrparams, entxml, soc_cryxml
        '@texture',  # soc_cryxml
        '@cubemapTexture',  # soc_cryxml
        '@externalLayerFilePath',  # soc_cryxml
    ]

    def process_p4k_file(path):
        ext = path.split('.', maxsplit=1)[1]
        p4k_info = sc.p4k.NameToInfoLower[path.lower()]
        monitor(f'process: ({ext}) {p4k_info.filename}')
        try:
            if ext in ['mtl', 'chrparams', 'entxml', 'rmp', 'animevents', 'cdf']:
                add_file_to_extract(dict_search(dict_from_cryxml_file(sc.p4k.open(p4k_info)), keys_with_paths))
            elif ext in ['cga', 'cgam', 'cgf', 'cgfm', 'chr', 'soc', 'dba', 'skin', 'skinm']:
                # ChCr, find material chunk `MtlName` and extract referenced material file
                raw = sc.p4k.open(p4k_info).read()
                c = Ivo(raw) if raw.startswith(b'#ivo') else ChCr(raw)
                for chunk in c.chunks.values():
                    if isinstance(chunk, ChCrChunks.CryXMLBChunk):
                        x = dict_from_cryxml_string(chunk.data)
                        add_file_to_extract(dict_search(x, keys_with_paths))
                        # Material keys don't have the extension
                        add_file_to_extract([f'{_}.mtl' for _ in dict_search(x, '@Material')])

                        # write out the extracted CryXMLB as json
                        out_path = outdir / f"{p4k_info.filename}.cryxml.json"
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        with out_path.open('w') as o:
                            json.dump(x, o, indent=4)
                    elif isinstance(chunk, ChCrChunks.JSONChunk):
                        x = chunk.dict()
                        add_file_to_extract(dict_search(x, keys_with_paths))
                        out_path = outdir / f"{p4k_info.filename}.json"
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        with out_path.open('w') as o:
                            json.dump(x, o, indent=4)
                    elif isinstance(chunk, (ChCrChunks.MtlName, ChCrChunks.MaterialName900)):
                        mtl_path = f'Data/{chunk.name}'.lower()
                        add_file_to_extract([_ for _ in sc.p4k.NameToInfoLower.keys() if _.startswith(mtl_path)])
            elif ext in 'xml':
                raw = sc.p4k.open(p4k_info).read()
                if raw.startswith(b'CryXmlB'):
                    x = dict_from_cryxml_string(raw)
                else:
                    x = etree_to_dict(ElementTree.fromstring(raw))
                add_file_to_extract(dict_search(x, keys_with_paths))
            else:
                monitor(f'WARN: unhandled p4k file: {path}')
        except Exception as e:
            monitor(f'ERROR: processing {path}: {e}')

    processed_files = set()
    while files_to_process:
        cur_files_to_process = files_to_process - processed_files
        files_to_process = set()
        for path in cur_files_to_process:
            process_p4k_file(path)  # processed files could add more files to process
        processed_files |= cur_files_to_process
    # endregion process files
    ##################################################################################################################

    ##################################################################################################################
    # region write all files to disk
    try:
        monitor('Extracting files')
        # filters = [f'*{f}*' for f in files_to_extract if f]
        # sc.p4k.extract_filter(filters, outdir, convert_cryxml=True, ignore_case=True)
        sc.p4k.extract_filter(files_to_extract, outdir, convert_cryxml=True, ignore_case=True,
                              search_mode='in_strip', monitor=monitor)
    except Exception as e:
        monitor(f'ERROR: Error extracting files {e}')
    monitor(f'Finished extracting {ship.name}')
    # endregion write all files to disk
    ##################################################################################################################

