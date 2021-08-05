import os
import json
import typing
import hashlib
from pathlib import Path

import tqdm

import bpy
import mathutils
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty, BoolProperty, CollectionProperty
from bpy.types import Operator, OperatorFileListElement

from scdatatools.blender import materials
from scdatatools.utils import redirect_to_tqdm
from scdatatools.blender.utils import (
    write_to_logfile, remove_proxy_meshes, remove_sc_physics_proxies, import_cleanup, log_time,
    deselect_all, collapse_outliner, copy_rotation, normalize_material_name, select_children
)


def hashed_path_key(geom_file):
    # hex digest is # chars/2
    h = hashlib.shake_128(geom_file.parent.as_posix().lower().encode("utf-8")).hexdigest(3)
    key = f'{h}_{Path(geom_file).stem.lower()}'
    if len(key) >= 64:
        key = f'{h}__{Path(geom_file).stem[len(key)-62:]}'
    assert(len(key) < 64)
    return key


def move_obj_to_collection(obj, collection):
    for c in obj.users_collection:
        c.objects.unlink(obj)
    collection.objects.link(obj)


def get_geometry_collection(geom_file: Path, geometry_collection, data_dir: Path = None, bone_names: list = None):
    """
    Returns the `Collection` for the givin `geom_file`. Imports the given geometry into `geometry_collection` if it has
    not already been imported.

    :param geom_file: `geom_file` to load. This is the relative path for the geometry from the `Data` dir
    :param data_dir: Local path to the root `Data` directory
    :param geometry_collection: the `Collection` to import the geometry into
    :return: The Collection for `geom_file`
    """
    if not isinstance(geom_file, Path):
        geom_file = Path(geom_file)

    geom_key = hashed_path_key(geom_file)
    data_dir = data_dir or ''
    bone_names = bone_names or []
    if not isinstance(data_dir, Path):
        data_dir = Path(data_dir)

    if geom_key in geometry_collection.children:
        # Already loaded, return the collection
        gc = geometry_collection.children[geom_key]
        if gc['filename'].lower() != geom_file.as_posix().lower():
            print(f'geom_file: {geom_file.as_posix()}')
            print(f'geom_key: {geom_key}')
            print(f'collection_entry: {gc.name}')
            print(f'collection_entry_filename: {gc["filename"]}')
            return gc
            # raise ValueError(f'geom_key collision! {geom_key}')
        return gc

    dae_file = (data_dir / geom_file).with_suffix('.dae')
    if not dae_file.is_file():
        print(f'WARNING: Skipping entity {geom_file.stem}: dae does not exist {dae_file}')
        return None

    try:
        deselect_all()
        # lc = bpy.context.view_layer.layer_collection.children[geometry_collection.name]
        # bpy.context.view_layer.active_layer_collection = lc
        old_mats = set(bpy.data.materials.keys())
        bpy.ops.wm.collada_import(filepath=dae_file.as_posix())
        new_mats = set(bpy.data.materials.keys()) - old_mats
    except Exception as e:
        print(f'ERROR: Error during collada import: {repr(e)}')
        return None

    gc = bpy.data.collections.new(geom_key)
    geometry_collection.children.link(gc)
    gc['filename'] = geom_file.as_posix()
    gc['materials'] = {}
    gc['tint_palettes'] = {}
    gc['tags'] = ""
    gc['item_ports'] = {}
    gc['objs'] = list(bpy.context.selected_objects)
    root_objs = []

    # move the imported objects into the new collection and namespace their names, also
    mats_to_del = set()

    for obj in gc['objs']:
        move_obj_to_collection(obj, gc)
        obj['orig_name'] = obj.name.rsplit('.', maxsplit=1)[0]
        if obj['orig_name'] in bone_names:
            gc['item_ports'][obj['orig_name']] = obj
        obj.name = hashed_path_key(Path(geom_key) / obj.name)
        if obj.parent is None:
            root_objs.append(obj)
        for slot in obj.material_slots:
            if not slot.material:
                continue

            norm_mat_name = normalize_material_name(slot.material.name)
            if norm_mat_name != slot.material.name:
                if slot.material.name in new_mats:
                    new_mats.remove(slot.material.name)
                if norm_mat_name in bpy.data.materials:
                    # we're using a duplicate name, reassign this slot and mark the 'new' duplicate mat for deletion
                    mats_to_del.add(slot.material.name)
                    slot.material = bpy.data.materials[norm_mat_name]
                else:
                    # norm name hasnt been setup yet, just rename this material to the right name
                    slot.material.name = norm_mat_name

    for mat in new_mats:
        if mat := bpy.data.materials.get(mat):
            norm_mat_name = normalize_material_name(mat.name)
            if norm_mat_name != mat.name:
                if norm_mat_name in bpy.data.materials:
                    mats_to_del.add(mat.name)
                else:
                    bpy.data.materials[mat.name].name = norm_mat_name

    for mat in mats_to_del:
        bpy.data.materials.remove(bpy.data.materials[mat])
    gc['root_objs'] = root_objs
    return gc


def create_geom_instance(geom_file: Path, entity_collection, geometry_collection, location=None, rotation=None,
                         scale=None, bone_name='', instance_name='', sub_geometry=None, parent=None,
                         bone_names=None, data_dir=None):
    # get the geometry collection for the geom_file
    gc = get_geometry_collection(geom_file, geometry_collection, data_dir=data_dir, bone_names=bone_names)
    if gc is None:
        return None

    # ignore the auto-generated instance numbers in the BP
    inst_name = f'{instance_name}' if (instance_name and not instance_name.isdigit()) else gc.name
    new_instance = bpy.data.objects.new(inst_name, None)
    new_instance.instance_type = 'COLLECTION'
    new_instance.instance_collection = gc

    # make the extra data readily available to users in the properties window for the instanced object
    new_instance['filename'] = gc['filename']
    new_instance['materials'] = gc['materials']
    new_instance['tint_palettes'] = gc['tint_palettes']
    new_instance['tags'] = gc['tags']
    new_instance['item_ports'] = gc['item_ports']
    entity_collection.objects.link(new_instance)

    # Duplicate the hierarchy of all the hardpoints from the collection as empty objects so we have clean
    # item_port names to attach other geometry (also makes the outliner look a lot nicer)
    par_map = {}

    def _build_hierarchy(obj):
        if obj.parent is None:
            par_map[obj['orig_name']] = new_instance
            return new_instance
        par = par_map[obj.parent["orig_name"]] if obj.parent["orig_name"] in par_map else _build_hierarchy(obj.parent)
        new_obj = bpy.data.objects.new(f'{obj["orig_name"]}', None)
        new_obj.location = obj.location
        new_obj.rotation_mode = "QUATERNION"
        copy_rotation(obj, new_obj)
        new_obj.parent = par
        par_map[obj["orig_name"]] = new_obj
        entity_collection.objects.link(new_obj)
        return new_obj

    new_instance['item_ports'] = {}
    for ip_name, gc_obj in gc['item_ports'].items():
        new_instance['item_ports'][ip_name] = _build_hierarchy(gc_obj)

    if isinstance(rotation, list):
        # 3x3 rotation matrix
        rot_matrix = mathutils.Matrix(rotation)
        rotation = rot_matrix.to_quaternion()
    elif isinstance(rotation, dict):
        # dict of a quaternion
        rotation = (rotation['w'], rotation['x'], rotation['y'], rotation['z'])
    else:
        rotation = (1, 0, 0, 0)

    new_instance.location = (0, 0, 0) if location is None else (location['x'], location['y'], location['z'])
    new_instance.rotation_mode = "QUATERNION"
    new_instance.scale = (1, 1, 1) if scale is None else (scale['x'], scale['y'], scale['z'])
    new_instance.rotation_quaternion = rotation
    if bone_name:
        if parent is not None and bone_name in parent['item_ports']:
            new_instance.parent = parent['item_ports'][bone_name]

    for geom_file, instances in (sub_geometry or {}).items():
        for props in instances:
            # create instances of all sub-geometry underneath/scoped to the instance we just created
            bone_name = props['attrs'].get('bone_name', '')
            sub_geom = create_geom_instance(geom_file, entity_collection, geometry_collection,
                                            location=props.get('pos'), rotation=props.get('rotation'),
                                            scale=props.get('scale'), bone_name=bone_name, instance_name='',
                                            data_dir=data_dir, bone_names=bone_names, parent=new_instance)
            if sub_geom is None:
                print(f'ERROR: failed to create sub-geometry "{geom_file}" under {new_instance}')
            elif sub_geom is not None and sub_geom.parent is None:
                if bone_name:
                    print(f"WARNING: could not parent sub_geometry {geom_file} to "
                          f"instance {new_instance.name}:{bone_name}")
                sub_geom.parent = new_instance

    return new_instance


class RemoveProxyMeshes(Operator):
    """ Removes Meshes with the "proxy" material """
    bl_idname = "scdt.remove_proxy_meshes"
    bl_label = "Remove Proxy Meshes"

    def execute(self, context):
        if remove_proxy_meshes():
            return {'FINISHED'}
        return {'CANCELLED'}


class RemoveSCPhysicsProxies(Operator):
    """ Removes SC $physics_proxy objects """
    bl_idname = "scdt.remove_sc_physics_proxies"
    bl_label = "Remove SC Physics Proxies"

    def execute(self, context):
        if remove_sc_physics_proxies():
            return {'FINISHED'}
        return {'CANCELLED'}


class MakeReal(Operator):
    """ Makes an imported instance "real" """
    bl_idname = "scdt.make_real"
    bl_label = "Make Instance Real"

    def execute(self, context):
        print(f'Collecting instances to make real...')
        root = [_ for _ in context.selected_objects if _.parent is None][0]
        deselect_all()
        select_children(root)
        instances = [root]
        for obj in bpy.context.selected_objects:
            if obj.instance_type == 'COLLECTION':
                instances.append(obj)

        for inst in tqdm.tqdm(instances, desc='Making instances real'):
            deselect_all()
            inst.select_set(True)
            bpy.ops.object.duplicates_make_real(use_base_parent=True, use_hierarchy=True)

        bpy.ops.outliner.orphans_purge(num_deleted=0)
        return {'FINISHED'}


class ImportSCDVBlueprint(Operator, ImportHelper):
    """ Imports a Blueprint created from SCDT """
    bl_idname = "scdt.import_sc_blueprint"
    bl_label = "Import SCDT Blueprint"

    files = CollectionProperty(
        name="File Path",
        type=OperatorFileListElement,
    )
    directory = StringProperty(
        subtype='DIR_PATH',
    )

    # ImportHelper mixin class uses this
    filename_ext = ".scbp"

    filter_glob: StringProperty(
        default="*.scbp",
        options={'HIDDEN'},
        maxlen=255,  # Max internal buffer length, longer would be clamped.
    )

    import_data_dir: StringProperty(
        name='Data Dir',
        default='',
        description=(
            "The Data directory containing the assets for the selected blueprint. If blank, this will look for "
            "Data next to the blueprint"
        )
    )

    remove_physics_proxies: BoolProperty(
        name="Auto-remove Physics Proxies",
        description="Automatically remove '$physics_proxy' objects after import",
        default=True,
    )

    auto_import_materials: BoolProperty(
        name="Auto-import Materials",
        description="Automatically import and fixup all referenced material files from the blueprint",
        default=True,
    )

    auto_remove_proxy_mesh: BoolProperty(
        name="Auto-remove Proxy Meshes",
        description="Automatically remove proxy meshes",
        default=True,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def execute(self, context):
        bp_file = Path(self.filepath)
        data_dir = (Path(self.import_data_dir) if self.import_data_dir else bp_file.parent / 'Data').absolute()
        if not data_dir.is_dir():
            write_to_logfile('Could not determine Data directory for blueprint')
            return {'CANCELLED'}

        if not 'sc_loaded_mats' in bpy.context.scene:
            pass

        print(f'Loading SCDV Blueprint: {bp_file}')
        print(f'Data dir: {data_dir}')
        bp = json.load(bp_file.open())

        for name in ['Output']:
            log_file = bpy.data.texts.get(name) or bpy.data.texts.new(name)
            log_file.clear()

        entity_collection = bpy.data.collections.new(f'{bp["name"]}')
        bpy.context.scene.collection.children.link(entity_collection)

        geom_collection = bpy.data.collections.new(f'{bp["name"]}_Geometry')
        entity_collection.children.link(geom_collection)
        entity_instance = None

        with log_time(f'Importing Blueprint {bp["name"]}'):
            parent_map = {}
            mats_to_load = set()

            with log_time('Importing Geometry'):
                # These could be loaded just-in-time, but loading them upfront makes the console output a lot nicer to
                # parse. TODO: change things once we have a working progress dialog
                for name, entity in tqdm.tqdm(bp['geometry'].items(), desc='Importing Geometry', postfix='',
                                              total=len(bp['geometry']), unit='g'):

                    geom_file = Path(entity['geom_file'])
                    gc = get_geometry_collection(geom_file, geom_collection, data_dir, bp['bone_names'])
                    if gc is None:
                        continue

                    if self.remove_physics_proxies:
                        # obj name will be scoped (geom_file.orig_name)
                        proxy_objs = [obj for obj in gc['objs']
                                      if obj.name.split('.')[-1].lower().startswith('$physics_proxy')]
                        if proxy_objs:
                            for obj in tqdm.tqdm(proxy_objs, desc='Removing SC physics proxy objects'):
                                bpy.data.objects.remove(obj, do_unlink=True)
                    for mat in entity['materials']:
                        if not mat:
                            continue
                        mat_name = Path(mat).stem.lower()
                        gc['materials'][mat_name] = (data_dir / mat).as_posix()
                        mats_to_load.add((data_dir / mat).as_posix())

                    gc['tags'] = entity['attrs'].get('tags', '')
                    if palette := entity['attrs'].get('palette', ''):
                        gc['tint_palettes'][hashed_path_key(Path(palette))] = palette

                for name, entity in tqdm.tqdm(bp['geometry'].items(), desc='Instancing Geometry', postfix='',
                                              total=len(bp['geometry']), unit='g'):
                    for i_name, i in entity['instances'].items():
                        geom_file = Path(entity['geom_file'])
                        new_instance = create_geom_instance(geom_file, entity_collection, geom_collection,
                                                            location=i['pos'], rotation=i['rotation'], scale=i['scale'],
                                                            bone_name=i['attrs'].get('bone_name', ''),
                                                            instance_name=i_name, data_dir=data_dir,
                                                            bone_names=bp['bone_names'], parent=entity_instance)
                        if new_instance is None:
                            # todo: this _shouldn't_ happen. if it does we really should figure out why
                            print(f'ERROR: could not create instance for {name}')
                            continue

                        if Path(new_instance.instance_collection['filename']).stem == bp['name'] or \
                                entity_instance is None:
                            entity_instance = new_instance
                            entity_instance.name = bp['name']

                        if new_instance.parent is None:
                            if bone_name := i['attrs'].get('bone_name', ''):
                                parent_map.setdefault(bone_name, []).append(new_instance)

                for port_name, props in tqdm.tqdm(bp['item_ports'].items(), desc='Importing Hardpoints',
                                                  total=len(bp['item_ports']), unit='h'):
                    if entity_instance and port_name in entity_instance['item_ports']:
                        for geom_name in props['geometry']:
                            if geom_name not in bp['geometry']:
                                continue

                            sub_geometry = bp['geometry'][geom_name].get('sub_geometry', {})
                            for item_port, loadout in props.get('loadout', {}).items():
                                for geom in loadout.get('geometry', []):
                                    sub_geometry.setdefault(geom, []).append({'attrs': {'bone_name': item_port}})

                            new_instance = create_geom_instance(geom_name, entity_collection=entity_collection,
                                                                geometry_collection=geom_collection,
                                                                bone_name=port_name, sub_geometry=sub_geometry,
                                                                data_dir=data_dir, bone_names=bp['bone_names'],
                                                                parent=entity_instance)

            with log_time('Post-import cleanup'):
                # parent items now that everything is loaded
                for bone_name, objs in parent_map.items():
                    if bone_name in entity_instance['item_ports']:
                        for obj in objs:
                            obj.parent = entity_instance['item_ports'][bone_name]

                import_cleanup(bpy.context, option_deleteproxymat=self.auto_remove_proxy_mesh)

            if self.auto_remove_proxy_mesh:
                with log_time('Removing proxy mesh objects'):
                    remove_proxy_meshes()

            if self.auto_import_materials:
                with log_time('Loading Materials'):
                    materials.load_materials(mats_to_load, data_dir)

            # hide the geometry collection from view - must be done _after_ we do cleanup otherwise we cant select the
            # objects
            ecl = bpy.context.window.view_layer.layer_collection.children[entity_collection.name]
            ecl.children[geom_collection.name].hide_viewport = True

            # TODO: this doesnt seem to work here? It'll work if you run it manually afterwards
            collapse_outliner()

        return {'FINISHED'}


def menu_func_import(self, context):
    self.layout.operator(ImportSCDVBlueprint.bl_idname, text=ImportSCDVBlueprint.bl_label)


def register():
    bpy.utils.register_class(ImportSCDVBlueprint)
    bpy.utils.register_class(RemoveProxyMeshes)
    bpy.utils.register_class(RemoveSCPhysicsProxies)
    bpy.utils.register_class(MakeReal)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.utils.unregister_class(ImportSCDVBlueprint)
    bpy.utils.unregister_class(RemoveProxyMeshes)
    bpy.utils.unregister_class(RemoveSCPhysicsProxies)
    bpy.utils.unregister_class(MakeReal)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
