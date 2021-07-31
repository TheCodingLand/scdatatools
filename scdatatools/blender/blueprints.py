import os
import json
import typing
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
    deselect_all, collapse_outliner
)


def import_assets(dae_file: Path, geometry_collection, instance=''):
    if dae_file.name not in geometry_collection.children:
        return None
        # # Have not imported yet, import the collection
        # if os.path.isfile(dae_file) is False:
        #     return None
        #
        # try:
        #     deselect_all()
        #     bpy.ops.wm.collada_import(filepath=dae_file.as_posix())
        # except Exception as e:
        #     print(f'ERROR: Error during collada import: {repr(e)}')
        #     return None
        #
        # geom_col = bpy.data.collections.new(dae_file.name)
        # geometry_collection.children.link(geom_col)
        #
        # for obj in bpy.context.selected_objects:
        #     for c in obj.users_collection:
        #         c.objects.unlink(obj)
        #     geom_col.objects.link(obj)
    # else:
    #     geom_col = geometry_collection.children[dae_file.name]
    geom_col = geometry_collection.children[dae_file.name]

    # lc = bpy.context.view_layer.layer_collection.children[entity_collection.name]
    # bpy.context.view_layer.active_layer_collection = lc
    new_instance = bpy.data.objects.new(f'{dae_file.stem}.{instance}' if instance else dae_file.stem, None)
    new_instance.instance_type = 'COLLECTION'
    new_instance.instance_collection = geom_col
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

    def execute(self, context):
        bp_file = Path(self.filepath)
        data_dir = (Path(self.import_data_dir) if self.import_data_dir else bp_file.parent / 'Data').absolute()
        if not data_dir.is_dir():
            write_to_logfile('Could not determine Data directory for blueprint')
            return {'CANCELLED'}

        print(f'Loading SCDV Blueprint: {bp_file}')
        print(f'Data dir: {data_dir}')
        bp = json.load(bp_file.open())

        for name in ['Output']:
            log_file = bpy.data.texts.get(name) or bpy.data.texts.new(name)
            log_file.clear()

        mats = set()

        entity_collection = bpy.data.collections.new(f'{bp["name"]}')
        bpy.context.scene.collection.children.link(entity_collection)

        geom_collection = bpy.data.collections.new(f'{bp["name"]}_Geometry')
        entity_collection.children.link(geom_collection)

        with log_time(f'Importing Blueprint {bp["name"]}'):
            parent_fixup = {}
            parent_map = {}

            with log_time('Importing Geometry'):
                geo_map = {}
                for name, entity in tqdm.tqdm(bp['geometry'].items(), desc='Importing Geometry', postfix='',
                                        total=len(bp['geometry']), unit='g'):

                    geom_file = Path(entity['geom_file'])
                    dae_file = data_dir / geom_file.parent / f'{geom_file.stem}.dae'

                    if not dae_file.is_file():
                        print(f'WARNING: Skipping entity {geom_file}: could not find dae ')
                        continue

                    try:
                        deselect_all()
                        lc = bpy.context.view_layer.layer_collection.children[entity_collection.name]
                        bpy.context.view_layer.active_layer_collection = lc
                        bpy.ops.wm.collada_import(filepath=dae_file.as_posix())
                    except Exception as e:
                        print(f'ERROR: Error during collada import: {repr(e)}')
                        continue

                    if self.remove_physics_proxies:
                        proxy_objs = [obj for obj in bpy.context.selected_objects
                                      if obj.name.lower().startswith('$physics_proxy')]
                        if proxy_objs:
                            for obj in tqdm.tqdm(proxy_objs, desc='Removing SC physics proxy objects'):
                                bpy.data.objects.remove(obj, do_unlink=True)

                    geo_map[name] = {
                        'dae_file': dae_file,
                        'objs': list(bpy.context.selected_objects),
                        'materials': entity['materials']
                    }
                    mats.update(entity['materials'])

                # Move everything into collections
                for name, g in tqdm.tqdm(geo_map.items(), desc='Creating Geometry Collections'):
                    if any(obj.name.split('.')[0] in bp['bone_names'] for obj in g['objs']):
                        # skip putting imported geometry with attachment points in collections, but track them to fix
                        # up parenting later
                        root = [obj for obj in g['objs'] if obj.parent is None]
                        assert(len(root) == 1)
                        parent_fixup[name] = root[0]
                        continue

                    gc = bpy.data.collections.new(g['dae_file'].name)
                    geom_collection.children.link(gc)
                    gc['materials'] = g['materials']

                    for obj in g['objs']:
                        for c in obj.users_collection:
                            c.objects.unlink(obj)
                        gc.objects.link(obj)

                # entity_object = bpy.data.objects.new(bp['name'], None)
                # bpy.context.scene.collection.objects.link(entity_object)

                for name, entity in tqdm.tqdm(bp['geometry'].items(), desc='Instancing Geometry', postfix='',
                                        total=len(bp['geometry']), unit='g'):
                    geom_file = Path(entity['geom_file'])
                    dae_file = data_dir / geom_file.parent / f'{geom_file.stem}.dae'
                    if not dae_file.is_file():
                        print(f'WARNING: missing converted geometry for {name}: {dae_file}')
                        continue

                    for i_name, i in entity['instances'].items():
                        new_instance = import_assets(dae_file, geometry_collection=geom_collection, instance=name)
                        if new_instance is None:
                            # todo: this _shouldn't_ happen. if it does we really should figure out why
                            try:
                                new_instance = parent_fixup.pop(entity['name'])
                            except KeyError:
                                print(f'ERROR: couldnt create instance for {name}')
                                continue
                        else:
                            new_instance.name = f'{new_instance.name}.{i_name}'
                            entity_collection.objects.link(new_instance)

                        new_instance.location = (i['pos']['x'], i['pos']['y'], i['pos']['z'])
                        new_instance.rotation_mode = "QUATERNION"
                        if isinstance(i['rotation'], list):
                            # 3x3 rotation matrix
                            rot_matrix = mathutils.Matrix(i['rotation'])
                            new_instance.rotation_quaternion = rot_matrix.to_quaternion()
                        else:
                            # dict of a quaternion
                            new_instance.rotation_quaternion = (i['rotation']['w'], i['rotation']['x'],
                                                                i['rotation']['y'], i['rotation']['z'])
                        new_instance.scale = (i['scale']['x'], i['scale']['y'], i['scale']['z'])
                        if bone_name := i['attrs'].get('bone_name', ''):
                            parent_map.setdefault(bone_name, []).append(new_instance)
                        # if new_instance.parent is None:
                        #     new_instance.parent = entity_object

                for port_name, props in tqdm.tqdm(bp['item_ports'].items(), desc='Importing Hardpoints',
                                                 total=len(bp['item_ports']), unit='h'):
                    if port_name in bpy.data.objects:
                        port = bpy.data.objects[port_name]

                        if parent := props.get('parent', ''):
                            parent_obj = bpy.data.objects.get(parent)
                            if parent_obj is not None:
                                for par_child in parent_obj.children:
                                    for o in par_child.children:
                                        if o.name.split('.')[0] == port_name:
                                            port = o
                                            break
                                    else:
                                        continue  # didn't find anything, keep looking in children
                                    break

                        for geom_name in props['geometry']:
                            if geom_name not in bp['geometry']:
                                continue

                            geom_file = Path(bp['geometry'][geom_name]['geom_file'])
                            dae_file = data_dir / geom_file.parent / f'{geom_file.stem}.dae'

                            new_instance = import_assets(dae_file, geometry_collection=geom_collection, instance=name)
                            if new_instance is None:
                                if geom_name not in parent_fixup:
                                    print(f'WARNING: Missing geometry: {entity["name"]}')
                                    continue
                                new_instance = parent_fixup.pop(geom_name)
                            else:
                                entity_collection.objects.link(new_instance)

                            new_instance.parent = port

            with log_time('Post-import cleanup'):
                # parent items now that everything is loaded
                for bone_name, objs in parent_map.items():
                    if bone_name in bpy.data.objects:
                        for obj in objs:
                            obj.parent = bpy.data.objects[bone_name]

                import_cleanup(bpy.context, option_deleteproxymat=self.auto_remove_proxy_mesh)

            if self.auto_remove_proxy_mesh:
                with log_time('Removing proxy mesh objects'):
                    remove_proxy_meshes()

            if self.auto_import_materials:
                with log_time('Importing materials'):
                    materials.load_materials([_ for _ in mats if _], data_dir=data_dir)

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
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.utils.unregister_class(ImportSCDVBlueprint)
    bpy.utils.unregister_class(RemoveProxyMeshes)
    bpy.utils.unregister_class(RemoveSCPhysicsProxies)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
