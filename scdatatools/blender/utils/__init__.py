import os
import shutil
import subprocess
import sys
import typing
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager

import tqdm

try:
    import bpy
except ImportError:
    pass  # not in blender

from . import ui_utils, validation


@contextmanager
def log_time(msg=''):
    start_time = datetime.now()
    if msg:
        print(msg)
    yield
    print(f'Finished {msg}{" " if msg else ""}in {datetime.now() - start_time}')


def available_blender_installations(include_paths: typing.List[Path] = None) -> dict:
    """ Return a dictionary of discovered Blender Installations where each value is the `Path` to the installation and
    a `bool` of whether or not the version's Python is compatible with scdatatools.


    ... code-block:: python

        available_blender_installations()
        {'2.93': {'path': WindowsPath('C:/Program Files/Blender Foundation/Blender 2.93'), 'compatible': True}}

    :param include_paths: Additional Blender directories to check
    """
    blender_installs = {}

    pyver = str(sys.hexversion)
    blender = 'blender.exe' if sys.platform == 'win32' else 'blender'

    include_paths = set(include_paths if include_paths is not None else [])
    if shutil.which(blender):
        include_paths.add(Path(shutil.which('blender')).parent)

    if sys.platform == 'win32':
        include_paths.update(_.parent for _ in (Path(os.environ['PROGRAMFILES']) / 'Blender Foundation').rglob(blender))

    for ip in include_paths:
        for b in ip.glob(blender):
            try:
                versions = [_.split() for _ in subprocess.run(
                                f'"{b}" -b --factory-startup --python-expr "import sys, bpy; '
                                f'print(\'VERCHECK\', sys.version.split(' ')[0], '
                                'sys.hexversion, bpy.app.version_string)"',
                                shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                ).stdout.decode('utf-8').split('\n') if _.startswith('VERCHECK')]
                if versions:
                    bv = versions[0][3].rsplit('.', maxsplit=1)[0]
                    blender_installs[bv] = {'path': b, 'compatible': versions[0][2] == pyver, 'pyver': versions[0][1]}
            except (subprocess.CalledProcessError, StopIteration):
                continue

    return blender_installs


def auto_format_sc_data_dir_path(preferences, context):
    """
    This function is called every time the default_sc_data_dir folder path is updated.
    """
    sc_data = Path(preferences.default_sc_data_dir)
    if sc_data.is_dir():
        preferences.incorrect_sc_data_dir_folder_path = False
        if preferences.default_sc_data_dir != sc_data.as_posix():
            preferences.default_sc_data_dir = sc_data.as_posix()
    else:
        preferences.incorrect_sc_data_dir_folder_path = True


def write_to_logfile(log_text, log_name="Output"):
    return
    # log_file = bpy.data.texts.get(log_name) or bpy.data.texts.new(log_name)
    # log_file.write(f"[{datetime.now()}] {log_text}\n")
    # print(f"[{datetime.now()}] {log_text}")


def search_for_data_dir_in_path(path):
    try:
        if not isinstance(path, Path):
            path = Path(path)
        return Path(*path.parts[:tuple(_.lower() for _ in path.parts).index('data')+1])
    except ValueError:
        return ''


def deselect_all():
    bpy.ops.object.select_all(action="DESELECT")
    for obj in bpy.context.selected_objects:
        obj.select_set(False)


def set_outliner_stete(state):
    area = next(a for a in bpy.context.screen.areas if a.type == 'OUTLINER')
    bpy.ops.outliner.show_hierarchy({'area': area}, 'INVOKE_DEFAULT')
    for i in range(state):
        bpy.ops.outliner.expanded_toggle({'area': area})
    area.tag_redraw()


def collapse_outliner():
    set_outliner_stete(2)


def expand_outliner():
    set_outliner_stete(1)


def select_children(obj):
    for child in obj.children:
        child.select_set(True)
        select_children(child)


def remove_proxy_meshes() -> bool:
    """ Remove Meshes for the `proxy` Material typically found in converted Star Citizen models. """
    # remove proxy meshes
    if 'proxy' not in bpy.data.materials:
        print("Could not find proxy material")
        return False

    cur_mode = bpy.context.active_object.mode if bpy.context.active_object is not None else 'OBJECT'
    try:
        bpy.ops.object.mode_set(mode='OBJECT')
        deselect_all()
        bpy.ops.object.select_by_type(type='MESH')
        bpy.ops.object.mode_set(mode='EDIT')

        bpy.context.object.active_material = bpy.data.materials['proxy']
        bpy.ops.object.material_slot_select()
        bpy.ops.mesh.delete(type='FACE')

        bpy.ops.object.mode_set(mode=cur_mode)
    except Exception as e:
        print(f'Failed to remove proxy meshes: {repr(e)}')
        return False
    return True


def remove_sc_physics_proxies() -> bool:
    """ Remove `$physics_proxy*` objects typically found in converted Star Citizen models. """
    # remove physics proxies
    try:
        proxy_objs = [obj for obj in bpy.data.objects if obj.name.lower().startswith('$physics_proxy')]
        for obj in tqdm.tqdm(proxy_objs, desc='Removing SC physics proxy objects'):
            bpy.data.objects.remove(obj, do_unlink=True)
        return True
    except Exception as e:
        print(f'Failed to remove sc physics proxies: {repr(e)}')
        return False


def import_cleanup(context, option_deleteproxymat=False, option_offsetdecals=False, option_cleanupimages=True):
    try:
        bpy.ops.material.materialutilities_merge_base_names(is_auto=True)
    except AttributeError:
        print(f'Material Utilities add-on not enabled: cannot fix up material base names')

    for obj in context.scene.objects:
        split = obj.name.split(".")
        obj.name = obj.name.replace("_out", "")
        # obj.name = obj.name.split(".")[0]
        # locators_objs = [
        #     obj for obj in bpy.data.objects if obj.name.startswith(split[0])
        # ]

        if obj.type == "MESH":
            obj.data.use_auto_smooth = True

            for index, slot in enumerate(obj.material_slots):
                # select the verts from faces with material index
                if not slot.material:
                    # empty slot
                    continue
                verts = [
                    v
                    for f in obj.data.polygons
                    if f.material_index == index
                    for v in f.vertices
                ]
                if "proxy" in slot.material.name.lower() and option_deleteproxymat:
                    obj.select_set(True)
                    bpy.context.view_layer.objects.active = obj
                    bpy.context.object.active_material_index = index
                    bpy.ops.object.mode_set(mode="EDIT")
                    bpy.ops.object.material_slot_select()
                    bpy.ops.mesh.delete(type="FACE")
                    bpy.ops.object.mode_set(mode="OBJECT")
                    bpy.ops.object.select_all(action="DESELECT")
                if len(verts):
                    vg = obj.vertex_groups.get(slot.material.name)
                    if vg is None:
                        vg = obj.vertex_groups.new(name=slot.material.name)
                    vg.add(verts, 1.0, "ADD")
                if (
                        ("pom" in slot.material.name)
                        or ("decal" in slot.material.name)
                        and option_offsetdecals
                ):
                    mod_name = slot.material.name + " tweak"
                    if not obj.modifiers.get(mod_name):
                        obj.modifiers.new(mod_name, "DISPLACE")
                        obj.modifiers[mod_name].vertex_group = slot.material.name
                        obj.modifiers[mod_name].strength = 0.001
                        obj.modifiers[mod_name].mid_level = 0

            if not obj.modifiers.get("Weighted Normal"):
                obj.modifiers.new("Weighted Normal", "WEIGHTED_NORMAL")
                obj.modifiers["Weighted Normal"].keep_sharp = True

        elif obj.type == "EMPTY":
            obj.empty_display_size = 0.1
            if "hardpoint" in obj.name:
                obj.show_name = False
                obj.empty_display_type = "SPHERE"
                obj.scale = (1, 1, 1)
                # obj.show_in_front = True
            elif "light" in obj.name:
                obj.empty_display_type = "SINGLE_ARROW"
            elif "$" in obj.name:
                obj.empty_display_type = "SPHERE"

        if "DM_" in obj.name:
            if bpy.data.collections.find("Damaged") == -1:
                bpy.data.collections.new("Damaged")
            # bpy.data.collections['Damaged'].objects.link(obj)
        elif "Interior" in obj.name:
            if bpy.data.collections.find("Interior") == -1:
                bpy.data.collections.new("Interior")
            # bpy.data.collections['Interior'].objects.link(obj)

    bpy.ops.outliner.orphans_purge(num_deleted=0)
    return {"FINISHED"}
