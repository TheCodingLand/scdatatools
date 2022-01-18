import os
import sys
import typing
import shutil
import logging
import hashlib
import subprocess
from itertools import chain
from pathlib import Path


import tqdm

try:
    import bpy
except ImportError:
    pass  # not in blender

from . import ui_utils, validation


logger = logging.getLogger(__name__)


def available_blender_installations(
    include_paths: typing.List[Path] = None, compatible_only=False
) -> dict:
    """Return a dictionary of discovered Blender Installations where each value is the `Path` to the installation and
    a `bool` of whether or not the version's Python is compatible with scdatatools.


    ... code-block:: python

        available_blender_installations()
        {'2.93': {'path': WindowsPath('C:/Program Files/Blender Foundation/Blender 2.93'), 'compatible': True}}

    :param include_paths: Additional Blender directories to check
    :param compatible_only: If `True` only return compatible versions of Blender
    """
    blender_installs = {}

    pyver = str(sys.hexversion)
    blender = "blender.exe" if sys.platform == "win32" else "blender"

    include_paths = set(include_paths if include_paths is not None else [])
    if shutil.which(blender):
        include_paths.add(Path(shutil.which("blender")).parent)

    if sys.platform == "win32":
        include_paths.update(
            _.parent
            for _ in (Path(os.environ["PROGRAMFILES"]) / "Blender Foundation").rglob(
                blender
            )
        )
    elif sys.platform == "darwin":
        include_paths.update(
            chain(
                *(
                    [b.parent for b in _.rglob(blender)]
                    for _ in Path("/Applications").glob("Blender*")
                )
            )
        )

    for ip in include_paths:
        for b in ip.glob(blender):
            try:
                versions = [
                    _.split()
                    for _ in subprocess.run(
                        f'"{b}" -b --factory-startup --python-expr "import sys, bpy; '
                        f"print('VERCHECK', sys.version.split("
                        ")[0], "
                        'sys.hexversion, bpy.app.version_string)"',
                        shell=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        timeout=5,
                    )
                    .stdout.decode("utf-8")
                    .split("\n")
                    if _.startswith("VERCHECK")
                ]
                if versions:
                    compatible = versions[0][2] == pyver
                    if compatible_only and not compatible:
                        continue
                    bv = versions[0][3].rsplit(".", maxsplit=1)[0]
                    blender_installs[bv] = {
                        "path": b,
                        "compatible": compatible,
                        "pyver": versions[0][1],
                    }
            except (
                subprocess.CalledProcessError,
                StopIteration,
                subprocess.TimeoutExpired,
            ):
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


def deselect_all():
    # bpy.ops.object.select_all(action="DESELECT")
    for obj in bpy.context.selected_objects:
        obj.select_set(False)


def set_outliner_state(state):
    ctx = bpy.context.copy()
    ctx["area"] = next(a for a in bpy.context.screen.areas if a.type == "OUTLINER")
    bpy.ops.outliner.show_hierarchy(ctx, "INVOKE_DEFAULT")
    for i in range(state):
        bpy.ops.outliner.expanded_toggle(ctx)
    ctx["area"].tag_redraw()


def collapse_outliner():
    set_outliner_state(2)


def expand_outliner():
    set_outliner_state(1)


def select_children(obj):
    for child in obj.children:
        child.select_set(True)
        select_children(child)


def remove_proxy_meshes() -> bool:
    """Remove Meshes for the `proxy` Material typically found in converted Star Citizen models."""
    # remove proxy meshes
    if "proxy" not in bpy.data.materials:
        print("Could not find proxy material")
        return False

    cur_mode = (
        bpy.context.active_object.mode
        if bpy.context.active_object is not None
        else "OBJECT"
    )
    try:
        bpy.ops.object.mode_set(mode="OBJECT")
        deselect_all()
        bpy.ops.object.select_by_type(type="MESH")
        bpy.ops.object.mode_set(mode="EDIT")

        bpy.context.object.active_material = bpy.data.materials["proxy"]
        bpy.ops.object.material_slot_select()
        bpy.ops.mesh.delete(type="FACE")

        bpy.ops.object.mode_set(mode=cur_mode)
    except Exception as e:
        print(f"Failed to remove proxy meshes: {repr(e)}")
        return False
    return True


def remove_sc_physics_proxies() -> bool:
    """Remove `$physics_proxy*` objects typically found in converted Star Citizen models."""
    # remove physics proxies
    try:
        proxy_objs = [
            obj
            for obj in bpy.data.objects
            if obj.name.lower().startswith("$physics_proxy")
        ]
        for obj in tqdm.tqdm(proxy_objs, desc="Removing SC physics proxy objects"):
            bpy.data.objects.remove(obj, do_unlink=True)
        return True
    except Exception as e:
        print(f"Failed to remove sc physics proxies: {repr(e)}")
        return False


def normalize_material_name(mat_name):
    if "_mtl_" in mat_name:
        # normalize mtl library name
        mtl_file, mtl = mat_name.split("_mtl_")
        norm_mat_name = f"{mtl_file.lower()}_mtl_{mtl}"
    else:
        norm_mat_name = mat_name

    if "." in norm_mat_name:
        # remove .NNN
        base_mat_name, _ = norm_mat_name.rsplit(".", maxsplit=1)
        norm_mat_name = base_mat_name if _.isdigit() else norm_mat_name

    return norm_mat_name


def import_cleanup(context, option_offsetdecals=False):
    objects = list(context.selected_objects)
    for obj in tqdm.tqdm(objects, total=len(objects), desc="Cleaning up objects"):
        obj.name = obj.name.replace("_out", "")

        if obj.type == "MESH":
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

    bpy.ops.outliner.orphans_purge(num_deleted=0)
    deselect_all()
    return {"FINISHED"}


# from the space_view3d_copy_attributes blender plugin
def copy_rotation(from_obj, to_obj):
    """Copy rotation to item from matrix mat depending on item.rotation_mode"""
    if to_obj.rotation_mode == "QUATERNION":
        to_obj.rotation_quaternion = from_obj.matrix_basis.to_3x3().to_quaternion()
    elif to_obj.rotation_mode == "AXIS_ANGLE":
        rot = (
            from_obj.matrix_basis.to_3x3().to_quaternion().to_axis_angle()
        )  # returns (Vector((x, y, z)), w)
        axis_angle = rot[1], rot[0][0], rot[0][1], rot[0][2]  # convert to w, x, y, z
        to_obj.rotation_axis_angle = axis_angle
    else:
        to_obj.rotation_euler = from_obj.matrix_basis.to_3x3().to_euler(
            to_obj.rotation_mode
        )


def str_to_tuple(instr, conv: typing.Callable = None) -> tuple:
    if conv is None:
        conv = str
    return tuple(conv(_.strip()) for _ in instr.split(","))


def hashed_path_key(path: Path) -> str:
    # hex digest is # chars/2
    if isinstance(path, str):
        path = Path(path)
    h = f'{hashlib.shake_128(path.parent.as_posix().lower().encode("utf-8")).hexdigest(3)}'
    name = f"{path.stem.lower()}".replace("{", "").replace("}", "").replace("/", "_")
    key = f"{h}_{name}"
    if len(key) >= 64:
        key = f"{h}__{name[len(key) - 62:]}"  # f'{h}__{key.split("_", maxsplit=1)[1][64 - (len(key) - len(h)-2):]}'
    assert len(key) < 64
    return key


def move_obj_to_collection(obj, collection):
    for c in obj.users_collection:
        c.objects.unlink(obj)
    collection.objects.link(obj)


def apply_transform(obj):
    """Apply the local transformation to an object without using bpy.ops.apply_transform"""
    mb = obj.matrix_basis
    if hasattr(obj.data, "transform"):
        obj.data.transform(mb)
    for c in obj.children:
        c.matrix_local = mb @ c.matrix_local
    obj.matrix_basis.identity()
