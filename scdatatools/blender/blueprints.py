import os
import json
from pathlib import Path

import tqdm

import bpy
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty, BoolProperty, CollectionProperty
from bpy.types import Operator, OperatorFileListElement

from scdatatools.blender import materials
from scdatatools.blender.utils import write_to_logfile


def select_children(obj):
    for child in obj.children:
        child.select_set(True)
        select_children(child)


def remove_proxy_meshes():
    """ Remove Meshes for the `proxy` Material typically found in converted Star Citizen models. """
    # remove proxy meshes
    if 'proxy' not in bpy.data.materials:
        print("Could not find proxy material")
        return

    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.object.select_by_type(type='MESH')
    cur_mode = bpy.context.active_object.mode if bpy.context.active_object is not None else 'OBJECT'
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.context.object.active_material = bpy.data.materials['proxy']
    bpy.ops.object.material_slot_select()
    bpy.ops.mesh.delete(type='FACE')
    bpy.ops.object.mode_set(mode=cur_mode)


def remove_sc_physics_proxies():
    """ Remove `$physics_proxy*` objects typically found in converted Star Citizen models. """
    # remove physics proxies
    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.object.delete({
        "selected_objects": [obj for obj in bpy.data.objects if obj.name.lower().startswith('$physics_proxy')]
    })


def import_assets(context, new_assetfilename, parent_map=None, option_import=True, option_fixorphans=False):
    parent_map = parent_map or {}
    new_assetfilename = new_assetfilename.lower()
    bpy.ops.object.select_all(action="DESELECT")
    for obj in context.selected_objects:
        obj.select_set(False)

    if new_assetfilename not in parent_map:
        if option_import:
            if os.path.isfile(new_assetfilename) is False:
                new_empty = bpy.data.objects.new("empty", None)
                new_empty.empty_display_type = "CUBE"
                return False
            try:
                bpy.ops.wm.collada_import(filepath=new_assetfilename)
            except:
                new_empty = bpy.data.objects.new("empty", None)
                new_empty.empty_display_type = "CUBE"
                new_empty["Filename"] = new_assetfilename
                return False

        new_assets = context.selected_objects

        if len(new_assets) == 0:
            print("Nothing created " + new_assetfilename)
            return False

        new_assets_parent = [
            obj for obj in new_assets if obj.type == "EMPTY" and "$" not in obj.name and obj.parent is None
        ]

        for obj in new_assets:
            obj["Filename"] = str(new_assetfilename)
            if option_fixorphans and ".Merged" in obj.name:
                print("Fixing " + obj.name)
                obj.name = Path(new_assetfilename).stem + ".Merged"
                print("Fixed " + obj.name)
                try:
                    obj.parent = new_assets_parent[0]
                    print("Re-parented " + obj.name + " to " + new_assets_parent[0].name)
                except:
                    print("Unable to re-parent " + obj.name)
        return True
    else:
        parent = parent_map[new_assetfilename]
        parent.select_set(True)
        select_children(parent)
        bpy.ops.object.duplicate(linked=True)
        bpy.context.selected_objects[0].parent = None
    return True


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

        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False, confirm=False)

        mats = set()
        parent_map = {}

        for entity in tqdm.tqdm(bp['geometry'].values(), desc='Importing Geometry', postfix='',
                                total=len(bp['geometry']), unit='g'):
            geom_file = Path(entity['geom_file'])
            dae_file = data_dir / geom_file.parent / f'{geom_file.stem}.dae'

            if not dae_file.is_file():
                print(f'WARNING: Skipping entity {geom_file}: could not find dae ')
                continue

            mats.update(entity['materials'])

            for name, i in entity['instances'].items():
                write_to_logfile(f'Importing {dae_file} [{name}]')

                if not import_assets(bpy.context, dae_file.as_posix(), parent_map=parent_map):
                    continue

                new_parents = [obj for obj in bpy.context.selected_objects if obj.parent is None]

                map_name = dae_file.as_posix().lower()
                if map_name not in parent_map:
                    parent_map[map_name] = new_parents[0]

                for obj in new_parents:
                    obj.location = (i['pos']['x'], i['pos']['y'], i['pos']['z'])
                    obj.rotation_mode = "QUATERNION"
                    obj.rotation_quaternion = (i['rotation']['w'], i['rotation']['x'],
                                               i['rotation']['y'], i['rotation']['z'])
                    obj.scale = (i['scale']['x'], i['scale']['y'], i['scale']['z'])
                    if bone_name := i['attrs'].get('bone_name', ''):
                        if bone_name in bpy.data.objects:
                            port = bpy.data.objects[bone_name]
                            obj.parent = port
                            bpy.ops.object.parent_clear(type='CLEAR_INVERSE')

        for port_name, geom in tqdm.tqdm(bp['item_ports'].items(), desc='Importing Hardpoints',
                                         total=len(bp['item_ports']), unit='h'):
            if port_name in bpy.data.objects:
                port = bpy.data.objects[port_name]
                for geom_name in geom:
                    if geom_name not in bp['geometry']:
                        continue

                    write_to_logfile(f'item_port {port_name} [{geom_name}]')
                    geom_file = Path(bp['geometry'][geom_name]['geom_file'])
                    dae_file = data_dir / geom_file.parent / f'{geom_file.stem}.dae'
                    if not import_assets(bpy.context, dae_file.as_posix(), parent_map=parent_map):
                        continue

                    new_parents = [obj for obj in bpy.context.selected_objects if obj.parent is None]
                    for obj in new_parents:
                        obj.parent = port
                        bpy.ops.object.parent_clear(type='CLEAR_INVERSE')

        if self.remove_physics_proxies:
            remove_sc_physics_proxies()

        if self.auto_remove_proxy_mesh:
            remove_proxy_meshes()

        if self.auto_import_materials:
            try:
                bpy.ops.material.materialutilities_merge_base_names(is_auto=True)
                materials.load_materials([_ for _ in mats if _], data_dir=data_dir)
            except AttributeError:
                write_to_logfile(f'Could not merge material base names, enable the Material: Material Utilities addon')

        return {'FINISHED'}


def menu_func_import(self, context):
    self.layout.operator(ImportSCDVBlueprint.bl_idname, text=ImportSCDVBlueprint.bl_label)


def register():
    bpy.utils.register_class(ImportSCDVBlueprint)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.utils.unregister_class(ImportSCDVBlueprint)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
