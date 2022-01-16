import typing
import logging
from pathlib import Path

from pyquaternion import Quaternion

from scdatatools.p4k import P4KInfo
from scdatatools.utils import norm_path
from scdatatools.sc.blueprints.base import Blueprint
from scdatatools.engine.chunkfile import chunks
from scdatatools.sc.blueprints.processors.lighting import process_light_object
from scdatatools.sc.blueprints.processors.p4k.socpak import process_soc
from scdatatools.engine.model_utils import vector_from_csv, quaternion_from_csv

logger = logging.getLogger(__name__)

if typing.TYPE_CHECKING:
    from scdatatools import StarCitizen


def blueprint_from_socpak(sc: 'StarCitizen', socpak: typing.Union[str, P4KInfo, Path], bp: Blueprint = None,
                          container_name: str = '', bone_name: str = '', attrs: dict = None,
                          monitor: typing.Callable = None) -> 'Blueprint':
    """
    Generates a `Blueprint` which can be used to extract and import the assets defined within the given Object Container


    :param sc: `StarCitizen` instance to search for data.
    :param socpak: `str | Path` path, or the `P4KInfo` of the `.sockpak` in `sc.p4k`
    :param bp: Optionally add the `Object Container` to the given `Blueprint`, otherwise a new Blueprint will be
        created. When adding to a `Blueprint`, the prefab will be added to whatever the current `container` is selected
        for in the `Blueprint`
    :param container_name: Name of the container. If blank the name of the socpak will be used
    :param bone_name: Name of the attachment point for this `.socpak`
    :param attrs: `dict` of additional attributes to include in the `container` in the `Blueprint`
    :param monitor: The output log handling function Blueprint will use in addition to `logging`
    :return: `Blueprint`
    """
    if isinstance(socpak, P4KInfo):
        p4k_path = Path(socpak.filename)
    else:
        p4k_path = Path(socpak)

    name = container_name if container_name else p4k_path.stem
    attrs = attrs or {}
    attrs['socpak'] = p4k_path.as_posix()

    if bp is None:
        bp = Blueprint(name, sc, monitor=monitor)

    oc = bp.sc.oc_manager.load_socpak(f'{p4k_path.as_posix()}'.lower())
    added_path = bp.add_file_to_extract(p4k_path, no_process=True)  # extract the socpak itself
    bp.add_file_to_extract([_.filename for _ in oc.socpak.filelist], no_process=True)

    bp.bone_names.add(bone_name.lower())
    # geom_attrs = {'bone_name': bone_name} if bone_name else {}
    geom_attrs = {}

    # we're processing it right away, so make sure we're not double processing it
    bp._processed_containers.add(added_path)

    with bp.set_current_container(name, attrs=attrs):
        for soc_name, soc in oc.socs.items():
            soc_path = norm_path(soc.soc_info.filename).lower()
            bp.current_container['socs'].append(soc_path)
            bp.add_file_to_extract(soc_path, no_process=True)
            process_soc(bp, soc, bone_name=bone_name, geom_attrs=geom_attrs)
            # soc_proc = ('path', norm_path(soc.soc_info.filename).lower())
            # if soc_proc in bp._entities_to_process:
            #     bp._entities_to_process.remove(soc_proc)
            # for ico_id, ico in soc.included_objects.items():
            #     try:
            #         bp.add_file_to_extract(ico.filenames)
            #         materials = ico.materials
            #         for obj in ico.objects:
            #             if isinstance(obj, chunks.IncludedObjectType1):
            #                 geom, _ = bp.get_or_create_geom(obj.filename)
            #                 geom.add_instance(
            #                     '', pos=obj.pos, rotation=obj.rotation, scale=obj.scale,
            #                     materials=materials, attrs=geom_attrs
            #                 )
            #     except Exception as e:
            #         bp.log(f'Error processing chunk {ico_id} in soc {p4k_path}', exc_info=e)
            # for cxml_id, cxml in soc.cryxml_chunks.items():
            #     d = cxml.dict()
            #     # Root can be Entities or SCOC_Entities
            #     entities = d.get('Entities', d.get('SCOC_Entities', {})).get('Entity')
            #     if isinstance(entities, dict):
            #         entities = [entities]  # only one entity in this cryxmlb
            #     for entity in entities:
            #         try:
            #             geom = None
            #             if entity.get('@EntityClass') in SOC_ENTITY_CLASSES_TO_SKIP:
            #                 continue  # TODO: handle these, see SOC_ENTITY_CLASSES_TO_SKIP
            #             elif 'EntityGeometryResource' in entity.get('PropertiesDataCore', {}):
            #                 geom, _ = bp.get_or_create_geom(
            #                     entity['PropertiesDataCore']['EntityGeometryResource']
            #                     ['Geometry']['Geometry']['Geometry']['@path']
            #                 )
            #             elif entity.get('@EntityClass') == 'TransitManager':
            #                 gateway_index = int(entity['SCTransitManager']['CarriageSpawnLocations']['SpawnLocation']['@gatewayIndex'])
            #                 gateway = entity['SCTransitManager']['TransitDestinations']['Destination'][gateway_index]
            #                 blueprint_from_socpak(
            #                     sc,
            #                     socpak=entity['PropertiesDataCore']['SCTransitManager']['carriageInterior']['@path'],
            #                     container_name=entity['@Name'], bp=bp,
            #                     attrs={
            #                         'pos': (
            #                             vector_from_csv(entity['@Pos']) +
            #                             vector_from_csv(gateway['Gateway']['@gatewayPos'])
            #                         ),
            #                         'rotation': (
            #                             quaternion_from_csv(entity.get('@Rotate', '1,0,0,0')) *
            #                             quaternion_from_csv(gateway['Gateway']['@gatewayQuat'])
            #                         )
            #                     }
            #                 )
            #                 continue
            #             elif 'Light' in entity.get('@EntityClass'):
            #                 process_light_object(bp, entity, bone_name)
            #                 continue
            #             elif ecguid := entity.get('@EntityClassGUID'):
            #                 base_geom_path = bp.geometry_for_record(bp.sc.datacore.records_by_guid.get(ecguid),
            #                                                         base=True)
            #                 geom, _ = bp.get_or_create_geom(base_geom_path)
            #             if geom is not None:
            #                 w, x, y, z = (float(_) for _ in entity.get('@Rotate', '1,0,0,0').split(','))
            #                 if '@Layer' in entity:
            #                     geom_attrs['layer'] = entity['@Layer']
            #                 geom.add_instance(
            #                     name=entity['@Name'],
            #                     pos=vector_from_csv(entity.get('@Pos', '0,0,0')),
            #                     rotation=Quaternion(x=x, y=y, z=z, w=w),
            #                     scale=vector_from_csv(entity.get('@Scale', '1,1,1')),
            #                     materials=[entity.get("@Material", '')],
            #                     attrs=geom_attrs
            #                 )
            #             else:
            #                 bp.log(f'WARNING: non-skipped soc EntityClass doesnt have geometry: '
            #                        f'{entity.get("@EntityClass")}', logging.WARNING)
            #         except Exception as e:
            #             bp.log(f'Failed to parse soc CryXmlB entity "{entity["@Name"]}"', exc_info=e)
        for child_guid, child in oc.children.items():
            try:
                blueprint_from_socpak(
                    sc, socpak=child['name'], container_name=child.get('entityName', ''), bp=bp, bone_name=bone_name,
                    attrs={
                        'pos': vector_from_csv(
                            child['pos'] if 'pos' in child else child['entdata'].get('@Pos', child.get('pos', '0,0,0'))
                        ),
                        'rotation': quaternion_from_csv(child.get('rot', '1,0,0,0')),
                    })
            except Exception as e:
                bp.log(f'Failed to load child container of {name}: {child["name"]} {e}')
    return bp
