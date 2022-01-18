# -*- coding: utf-8 -*-
import math
import logging

import scdatatools.blender.materials
from math import sin
from pathlib import Path

import bpy
import bpy_extras
from mathutils import Quaternion

from scdatatools.blender.utils import str_to_tuple
from scdatatools.blender.materials.utils import create_light_texture

logger = logging.getLogger(__name__)


def set_light_state(light_obj, state):
    if "states" not in light_obj or state not in light_obj["states"]:
        logger.debug(f"could not set light state {state} for {light_obj.name}")
        return

    light_obj.data.color = light_obj["states"][state]["color"]
    # adjust intensity by +6EV; make this a user settings later
    light_obj.data.energy = light_obj["states"][state]["intensity"] * pow(2, 6)
    if light_obj["use_temperature"]:
        if temp_node := light_obj.data.node_tree.nodes.get("Temperature"):
            temp_node.inputs[0].default_value = light_obj["states"][state][
                "temperature"
            ]


def set_linked_light(obj_name, color, temp=None):
    for obj in find_obj_by_name(obj_name, True):
        if obj.type != "MESH":
            continue
        obj["light_link"] = [0, 0, 0]
        if temp:
            obj["light_link_temp"] = temp
        if not "_RNA_UI" in obj:
            obj["_RNA_UI"] = {}
        rna_ui = obj.get("_RNA_UI")
        rna_ui.update({"light_link": obj["light_link"]})
        obj["_RNA_UI"]["light_link"] = {
            "softmin": 0.0,
            "softmax": 1.0,
            "default": [0.0, 0.0, 0.0],
            "subtype": "COLOR",
            "description": "node_outline_prop",
        }
        obj["light_link"] = [color[2], color[1], color[0]]  # BGR
    return


def find_obj_by_name(obj_name, get_children=False):
    found_objs = []
    obj_name = obj_name.lower()
    for obj in bpy.data.objects:
        if obj_name in obj.name.lower():
            found_objs.append(obj)
            if get_children:
                for child_obj in obj.children:
                    found_objs.append(child_obj)
    return found_objs


def create_light(
    name,
    light,
    light_group_collection,
    state="defaultState",
    bone_name="",
    parent=None,
    data_dir: Path = None,
):
    lightType = light["EntityComponentLight"]["@lightType"]
    bulbRadius = float(
        light["EntityComponentLight"]["sizeParams"].get("@bulbRadius", 0.01)
    )
    lightRadius = float(
        light["EntityComponentLight"]["sizeParams"].get("@lightRadius", 1)
    )
    use_temperature = bool(int(light["EntityComponentLight"].get("@useTemperature", 1)))
    texture = light["EntityComponentLight"]["projectorParams"].get("@texture", "")
    fov = float(light["EntityComponentLight"]["projectorParams"].get("@FOV", 179))
    focusedBeam = float(
        light["EntityComponentLight"]["projectorParams"].get("@focusedBeam", 1)
    )
    shadowCasting = float(
        light["EntityComponentLight"]["shadowParams"].get("@shadowCasting", 1)
    )

    # TODO: EntityComponentLight.defaultState.lightStyle?
    # TODO: use shadowParams.@shadowCasting?
    bulbRadius = (
        bulbRadius * 0
    )  # set to zero for hard IES light edges, increase for softness

    if lightType == "Projector":
        # Spot lights
        light_data = bpy.data.lights.new(name=light_group_collection.name, type="SPOT")
        light_data.spot_size = math.radians(fov)
        light_data.spot_blend = focusedBeam
        light_data.shadow_soft_size = bulbRadius
        # light_data = bpy.data.lights.new(name=light_group_collection.name, type="AREA")
        # light_data.spread = math.radians(fov)
        # light_data.size = bulbRadius
    else:
        # Point Lights
        light_data = bpy.data.lights.new(name=name, type="POINT")
        light_data.shadow_soft_size = bulbRadius

    light_data.use_nodes = True

    light_obj = bpy.data.objects.new(name=name, object_data=light_data)
    light_obj["use_temperature"] = use_temperature
    light_obj["states"] = {}
    # light_obj.show_axis = True #for debugging. Remove before flight

    for key, val in light["EntityComponentLight"].items():
        if not key.endswith("State"):
            continue
        light_obj["states"][key] = {
            "color": (
                float(val["color"].get("@r", 1)),
                float(val["color"].get("@g", 1)),
                float(val["color"].get("@b", 1)),
            ),
            "intensity": float(val.get("@intensity", 1)),
            "temperature": float(val.get("@temperature", 5200)),
            "presetTag": val.get("@presetTag", ""),
            "lightStyle": int(val.get("@lightStyle", 0)),
        }

    if lightRadius > 0:
        light_data.cutoff_distance = lightRadius

    temp_node = light_obj.data.node_tree.nodes["Emission"]

    if texture and data_dir is not None:
        tex_path = data_dir / texture
        light_data["texture"] = tex_path.as_posix()
        ies_group = light_data.node_tree.nodes.new(type="ShaderNodeGroup")
        if ies_group is not None:
            ies_group.node_tree = create_light_texture(tex_path)
            if ies_group.node_tree is not None:
                ies_group.location.x -= 200
                light_obj.data.node_tree.links.new(
                    ies_group.outputs["Color"], temp_node.inputs["Color"]
                )
                temp_node.inputs["Strength"].default_value = 8
                temp_node = ies_group
                if light_data.type == "SPOT":
                    spot_size = (
                        sin(light_data.spot_size) / 2
                    )  # convert radians to normal
                    ies_group.inputs["Scale"].default_value = (
                        spot_size,
                        spot_size,
                        spot_size,
                    )

    if use_temperature:
        bb_node = light_obj.data.node_tree.nodes.new(type="ShaderNodeBlackbody")
        bb_node.name = "Temperature"
        bb_node.inputs[0].default_value = 3500  #
        light_obj.data.node_tree.links.new(
            bb_node.outputs["Color"], temp_node.inputs["Color"]
        )
        # light_obj.data.color = (1,1,1)

    # if shadowCasting == 0:
    #   light_data.cycles.cast_shadow = False
    # else:
    #    light_data.cycles.cast_shadow = True

    location = str_to_tuple(light["@Pos"], float)
    rotation_quaternion = Quaternion((1, 0, 0, 0))  # initial rotation X+
    rotation_quaternion = rotation_quaternion.cross(
        Quaternion((str_to_tuple(light["@Rotate"], float)))
    )

    bone_name = bone_name if bone_name else light.get("attrs", {}).get("bone_name", "")
    if parent is not None:
        if bone_name:
            if helper := parent.get("helpers", {}).get(bone_name.lower(), {}):
                bone_name = helper["name"]
                if "pos" in helper:
                    location = (
                        helper["pos"]["x"],
                        helper["pos"]["y"],
                        helper["pos"]["z"],
                    )
                if "scale" in helper:
                    scale = (
                        helper["scale"]["x"],
                        helper["scale"]["y"],
                        helper["scale"]["z"],
                    )
                if "rotation" in helper:
                    rotation = (
                        helper["rotation"]["w"],
                        helper["rotation"]["x"],
                        helper["rotation"]["y"],
                        helper["rotation"]["z"],
                    )
        if bone_name.lower() in parent.get("item_ports", {}):
            light_obj.parent = bpy.data.objects.get(
                parent["item_ports"][bone_name.lower()]
            )
        else:
            light_obj.parent = parent

    location = [
        sum(_)
        for _ in zip(
            location,
            str_to_tuple(
                light.get("RelativeXForm", {}).get("@translation", "0,0,0"), float
            ),
        )
    ]

    q = Quaternion(
        str_to_tuple(light.get("RelativeXForm", {}).get("@rotation", "1,0,0,0"), float)
    )
    rotation_quaternion = rotation_quaternion.cross(Quaternion((q)))

    initial_matrix = bpy_extras.io_utils.axis_conversion(
        from_forward="Y", from_up="Z", to_forward="Y", to_up="-X"
    )
    rotation_quaternion = rotation_quaternion.cross(initial_matrix.to_quaternion())

    # new_lightobject.matrix_parent_inverse.identity()
    light_obj.location = location
    light_obj.rotation_mode = "QUATERNION"
    light_obj.rotation_quaternion = rotation_quaternion
    light_obj.rotation_quaternion = light_obj.matrix_world.to_quaternion().cross(
        bpy_extras.io_utils.axis_conversion(
            from_forward="X", from_up="Y", to_forward="Y", to_up="-Z"
        ).to_quaternion()
    )
    light_obj.rotation_quaternion.rotate(
        Quaternion(
            str_to_tuple(
                light.get("RelativeXForm", {}).get("@rotation", "1,0,0,0"), float
            )
        )
    )

    light_obj.scale = (0.01, 0.01, 0.01)

    light_group_collection.objects.link(light_obj)

    set_light_state(light_obj, state)

    if light.get("GeomLink"):
        if light["GeomLink"].get("@SubObjectName"):
            light_obj["GeomLink"] = light["GeomLink"].get("@SubObjectName")
            set_linked_light(
                light["GeomLink"].get("@SubObjectName"), light_obj.data.color
            )

    logger.debugscbp(f"created light {light_obj.name} in {light_group_collection.name}")

    return light_obj
