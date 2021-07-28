"""
 preImport.py will load all the assets referenced in a prefab xml file, and give you a list for conversion
"""

import bpy
import glob
import math
import os.path
from pathlib import Path
from datetime import datetime
from ast import literal_eval as make_tuple
from xml.etree import cElementTree as ElementTree

# ImportHelper is a helper class, defines filename and
# invoke() function which calls the file selector.
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty, BoolProperty, EnumProperty
from bpy.types import Operator

from .utils import write_to_logfile, search_for_data_dir_in_path


log_files = "Geometry"
log_mats = "Materials"
log_errors = "Errors"
xml_parent = "./PrefabLibrary/Prefab/Objects/Object"
xml_property = "Prefab"


def preimport_prefab(
    context,
    xml_path,
    data_dir,
    option_fixorphans=True,
    option_findmtls=True,
    option_import=True,
):
    file_list = []
    mat_list = []
    try:
        xml_root = ElementTree.parse(str(xml_path)).getroot()
    except Exception as e:
        print("Unable to open XML: " + str(e))
        raise

    prefab_name = Path(xml_path).stem
    prefab_collection = bpy.data.collections.get(prefab_name) or bpy.data.collections.new(prefab_name)
    if not context.scene.collection.children.get(prefab_name):
        context.scene.collection.children.link(prefab_collection)
    viewlayer = context.view_layer.layer_collection.children.get(prefab_name)
    if viewlayer:
        context.view_layer.active_layer_collection = viewlayer

    for element in xml_root.findall(".//*[@Prefab]"):
        filename = element.attrib["Prefab"]
        path = data_dir / filename.lower()
        if path not in file_list:
            file_list.append(path)

    for element in xml_root.findall(".//*[@Material]"):
        filename = element.attrib["Material"]
        path = data_dir / (filename.lower() + ".mtl")
        if path not in mat_list:
            mat_list.append(path)

    if option_import:
        log_text = bpy.data.texts.get(log_errors) or bpy.data.texts.new(log_errors)
        for file in file_list:
            dae_filename = file.with_suffix('.dae')
            try:
                bpy.ops.wm.collada_import(filepath=dae_filename.as_posix())
            except Exception as e:
                log_text.write(f"Import Failed: {file }\n")
                continue
            import_obj = context.selected_objects
            for obj in import_obj:
                obj["Filename"] = dae_filename.as_posix()
                obj['Material'] = read_material_from_dae(dae_filename)
                if obj.type == 'MESH':
                    obj.data['Filename'] = dae_filename
                try:
                    bpy.data.collections[xml_path].objects.link(obj)
                except:
                    pass

    if option_fixorphans:
        for obj in context.scene.objects:
            if "Merged" in obj.name:
                filename = Path(obj["Filename"]).stem
                obj.name = filename + ".Merged"
                if (
                    context.scene.objects.get(filename)
                    and context.scene.objects.get(filename).type == "EMPTY"
                ):
                    print("found parent " + filename)
                    obj.parent = context.scene.objects[filename]

    if option_findmtls:
        for file in file_list:
            folder = file.parent.glob("*.mtl")
            for mtl in folder:
                if not mtl in mat_list:
                    mat_list.append(mtl)

    # one last pass to tag the root parent nodes
    for obj in context.scene.objects:
        if obj.parent is None:
            obj["Root"] = True

    # process and spit out logs
    file_list.sort()
    mat_list.sort()
    print("\n")
    log_text = bpy.data.texts.get(log_files) or bpy.data.texts.new(log_files)

    # if file_list: file_list = file_list.sort()
    for file in file_list:
        print(file)
        log_text.write(str(file) + "\n")
    log_text = bpy.data.texts.get(log_mats) or bpy.data.texts.new(log_mats)

    for mat in mat_list:
        print(mat)
        log_text.write(f"{mat}\n")

    return {"FINISHED"}


def import_cleanup(context, option_deleteproxymat=True, option_offsetdecals=False, option_cleanupimages=True):
    bpy.ops.material.materialutilities_merge_base_names(is_auto=True)

    for obj in context.scene.objects:
        split = obj.name.split(".")
        obj.name = obj.name.replace("_out", "")
        # obj.name = obj.name.split(".")[0]
        locators_objs = [
            obj for obj in bpy.data.objects if obj.name.startswith(split[0])
        ]

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
                if "proxy" in slot.material.name and option_deleteproxymat:
                    print(obj.name)
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
            elif "$physics" in obj.name:
                bpy.data.objects.remove(obj, do_unlink=True)
                continue

        if "DM_" in obj.name:
            if bpy.data.collections.find("Damaged") == -1:
                bpy.data.collections.new("Damaged")
            # bpy.data.collections['Damaged'].objects.link(obj)
        elif "Interior" in obj.name:
            if bpy.data.collections.find("Interior") == -1:
                bpy.data.collections.new("Interior")
            # bpy.data.collections['Interior'].objects.link(obj)

    if option_cleanupimages:
        for img in bpy.data.images:
            if "." not in img.name_full:
                continue
            if '.dds' in img.filepath:
                for ext in ['.tif', '.png']:
                    imgfile = Path(img.filepath).with_suffix(ext)
                    if imgfile.is_file():
                        newimg = bpy.data.images.load(imgfile.as_posix(), check_existing=True)
                        break
                else:
                    print(f'Could not find image: {img.filepath}')
                    continue
                img.user_remap(newimg)
                img.filepath = imgfile.as_posix()
                print(img.name_full + " -> " + newimg.name_full)
            head, tail = img.name_full.rsplit(".", 1)
            if bpy.data.images.get(head):
                print(img.name_full + " -> " + head)
                img.user_remap(bpy.data.images.get(head))
            elif tail.isdigit():
                print(img.name_full + " is now " + head)
                img.name = head

    bpy.ops.outliner.orphans_purge(num_deleted=0)
    return {"FINISHED"}


def import_prefab(
    context,
    xml_path,
    data_dir,
    option_brushes=True,
    option_component=True,
    option_lights=False,
    option_spawn=False,
    # option_preconvert=False,
    option_fixorphans=True,
    option_import=True,
):
    xml_root = ElementTree.parse(xml_path).getroot()

    for element in xml_root:
        root_empty = bpy.data.objects.new("empty", None)
        root_empty.name = element.get("Name") + ".root"
        root_empty["_id"] = element.get("Id")
        context.scene.collection.objects.link(root_empty)
        write_to_logfile(
            "Processing " + element.get("Name") + " - Entities: " + str(len(element[0]))
        )
        total_elements = str(len(element[0]))
        # prefab_name = element.get('Name')
        index_elements = 0
        if index_elements > 1:
            break
        for subelement in element[0]:
            index_elements += 1
            if option_brushes and subelement.get("Type") == "Brush":
                write_to_logfile(subelement.get("Type") + ": " + subelement.get("Name"))
                new_assetfilename = (data_dir / subelement.get("Prefab")).with_suffix('.dae')
                if subelement.get("Material"):
                    write_to_logfile(
                        data_dir / (str(subelement.get("Material")) + ".mtl"),
                        "Material",
                    )
                new_assets = [
                    obj
                    for obj in context.scene.objects
                    if obj.get("_id") == new_assetfilename
                ]
                if len(new_assets) == 0:
                    if not import_assets(context, new_assetfilename.as_posix(), option_import=option_import,
                                         option_fixorphans=option_fixorphans):
                        continue
                new_asset = get_root_parent(context.selected_objects)
                if new_asset is None:
                    write_to_logfile(f"Root not found for {new_assetfilename}", "Error")
                    continue
                new_assets = context.selected_objects
                new_asset.name = subelement.get("Name")

                set_property(new_assets, "Type", subelement.get("Type"))
                set_property(new_assets, "Prefab", subelement.get("Name"))
                set_property(new_assets, "Layer", subelement.get("Layer"))
                set_property(new_assets, "_id", subelement.get("Id"))

                # new_asset.parent = root_empty
                # new_asset.matrix_parent_inverse.identity()
                if subelement.get("Pos"):
                    new_asset.location = make_tuple(str(subelement.get("Pos")))
                new_asset.rotation_mode = "QUATERNION"
                if subelement.get("Rotate"):
                    new_asset.rotation_quaternion = make_tuple(subelement.get("Rotate"))
                if subelement.get("Scale"):
                    new_asset.scale = make_tuple(subelement.get("Scale"))
                if element.get("Name"):
                    add_to_collection(context, element.get("Name"), new_assets)
                # context.scene.collection.objects.link(new_asset)
            elif option_component and subelement.get("Type") == "EntityWithComponent":
                write_to_logfile(subelement.get("Type") + ": " + subelement.get("Name"))
                if subelement[0][0].find("Properties") == "NoneType":
                    continue
                new_assetfilename = (data_dir / str(
                    subelement[0][0].find("Properties").get("FilePath")
                )).with_suffix('.dae')
                if subelement.get("Material"):
                    write_to_logfile(
                        data_dir / (str(subelement.get("Material")) + ".mtl"),
                        "Material",
                    )
                if not import_assets(context, new_assetfilename.as_posix(), option_import=option_import):
                    continue
                new_asset = get_root_parent(context.selected_objects)
                new_assets = context.selected_objects
                new_asset.name = subelement.get("Name")
                set_property(new_assets, "Type", subelement.get("Type"))
                set_property(new_assets, "Prefab", subelement.get("Name"))
                set_property(new_assets, "Layer", subelement.get("Layer"))
                set_property(new_assets, "_id", subelement.get("Id"))
                # new_asset.parent = root_empty
                # new_asset.matrix_parent_inverse.identity()
                if subelement.get("Pos"):
                    new_asset.location = make_tuple(str(subelement.get("Pos")))
                new_asset.rotation_mode = "QUATERNION"
                if subelement.get("Rotate"):
                    new_asset.rotation_quaternion = make_tuple(subelement.get("Rotate"))
                if subelement.get("Scale"):
                    new_asset.scale = make_tuple(subelement.get("Scale"))
                if element.get("Name"):
                    add_to_collection(context, element.get("Name"), new_assets)
                # context.scene.collection.objects.link(new_asset)
            elif (
                option_lights
                and subelement.get("Type") == "Entity"
                and subelement.get("EntityClass") == "Light"
            ):

                write_to_logfile(subelement.get("Type") + ": " + subelement.get("Name"))

                lightType = subelement.findall(
                    "./PropertiesDataCore/EntityComponentLight"
                )[0].get("lightType")
                useTemperature = subelement.findall(
                    "./PropertiesDataCore/EntityComponentLight"
                )[0].get("useTemperature")
                bulbRadius = (
                    subelement.findall(
                        "./PropertiesDataCore/EntityComponentLight/sizeParams"
                    )[0].get("bulbRadius")
                    or 0.01
                )
                planeHeight = (
                    subelement.findall(
                        "./PropertiesDataCore/EntityComponentLight/sizeParams"
                    )[0].get("PlaneHeight")
                    or 1
                )
                planeWidth = (
                    subelement.findall(
                        "./PropertiesDataCore/EntityComponentLight/sizeParams"
                    )[0].get("PlaneWidth")
                    or 1
                )
                color_r = (
                    subelement.findall(
                        "./PropertiesDataCore/EntityComponentLight/defaultState"
                    )[0].get("r")
                    or 1
                )
                color_g = (
                    subelement.findall(
                        "./PropertiesDataCore/EntityComponentLight/defaultState"
                    )[0].get("g")
                    or 1
                )
                color_b = (
                    subelement.findall(
                        "./PropertiesDataCore/EntityComponentLight/defaultState"
                    )[0].get("b")
                    or 1
                )
                intensity = (
                    subelement.findall(
                        "./PropertiesDataCore/EntityComponentLight/defaultState"
                    )[0].get("intensity")
                    or 1
                )
                temperature = (
                    subelement.findall(
                        "./PropertiesDataCore/EntityComponentLight/defaultState"
                    )[0].get("temperature")
                    or 1
                )
                texture = (
                    subelement.findall(
                        "./PropertiesDataCore/EntityComponentLight/projectorParams"
                    )[0].get("texture")
                    or False
                )
                fov = (
                    subelement.findall(
                        "./PropertiesDataCore/EntityComponentLight/projectorParams"
                    )[0].get("FOV")
                    or 179
                )
                focusedBeam = (
                    subelement.findall(
                        "./PropertiesDataCore/EntityComponentLight/projectorParams"
                    )[0].get("focusedBeam")
                    or 1
                )

                bulbRadius = float(bulbRadius) * 0.01

                # if subelement.find('Properties').get('bActive')=="0": continue
                # if subelement.findall('.//Projector')[0].get('texture_Texture') == ("" or " "): continue
                if lightType == "Projector":
                    # Area lights
                    new_lightdata = bpy.data.lights.get("Name") or bpy.data.lights.new(
                        name=subelement.get("Name"), type="SPOT"
                    )
                    # new_lightdata.shape = "RECTANGLE"
                    new_lightdata.spot_size = math.radians(float(fov))
                    new_lightdata.spot_blend = float(focusedBeam)
                    # new_lightdata.size = float(planeHeight)
                    # new_lightdata.size_y = float(planeWidth)
                else:
                    # Point Lights
                    new_lightdata = bpy.data.lights.get("Name") or bpy.data.lights.new(
                        name=subelement.get("Name"), type="POINT"
                    )
                    # new_lightdata.spot_size = math.radians(float(fov))
                    new_lightdata.shadow_soft_size = float(bulbRadius)
                    # writetoLog("Spot Size " + str(new_lightdata.spot_size))
                    # new_lightdata.spot_blend = float(focusedBeam)

                new_lightdata.color = (color_r, color_g, color_b)
                new_lightdata.photographer.use_light_temperature = bool(
                    int(useTemperature)
                )
                new_lightdata.photographer.light_temperature = float(temperature)
                new_lightdata.energy = float(intensity) * 100
                new_lightdata.use_nodes = True
                if texture:
                    ies_name = data_dir / str(texture)
                    new_lightdata["Texture"] = ies_name
                    ies_group = new_lightdata.node_tree.nodes.new(
                        type="ShaderNodeGroup"
                    )
                    ies_group.node_tree = createLightTexture(ies_name)
                    ies_group.location.x -= 200
                    new_lightdata.node_tree.links.new(
                        ies_group.outputs[0],
                        new_lightdata.node_tree.nodes["Emission"].inputs[0],
                    )
                new_lightobject = bpy.data.objects.new(
                    name=subelement.get("Name"), object_data=new_lightdata
                )
                new_lightobject["Type"] = subelement.get("Type")
                new_lightobject["_id"] = subelement.get("Id")
                # new_lightobject.parent = root_empty
                new_lightobject.matrix_parent_inverse.identity()
                new_lightobject.location = make_tuple(subelement.get("Pos"))
                if subelement.get("Rotate"):
                    new_lightobject.rotation_mode = "QUATERNION"
                    new_lightobject.rotation_quaternion = makeQuatTuple(
                        subelement.get("Rotate")
                    )
                    new_lightobject.rotation_mode = "XYZ"
                    new_lightobject.rotation_euler[0] += 1.5708
                    new_lightobject.rotation_mode = "QUATERNION"
                new_lightobject_radius = 0.1
                new_lightobject.scale = (
                    new_lightobject_radius,
                    new_lightobject_radius,
                    new_lightobject_radius * -1,
                )
                new_lightobject.data.shadow_soft_size = float(bulbRadius)
                if subelement.get("Layer"):
                    add_to_collection(context, element.get("Name"), new_lightobject)
                context.scene.collection.objects.link(new_lightobject)
                if element.get("Name"):
                    add_to_collection(context, element.get("Name"), new_lightobject)
            elif (
                option_spawn
                and subelement.get("Type") == "Entity"
                and subelement.get("EntityClass") == "DynamicHangarVehicleSpawn"
            ):
                # writetoLog(subelement.get('Type') + ": " + subelement.get('Name'))
                new_empty = bpy.data.objects.new("empty", None)
                new_empty.name = subelement.get("Name")
                new_asset["Type"] = subelement.get("Type")
                new_asset["_id"] = subelement.get("Id")
                # new_empty.parent = root_empty
                new_empty.location = make_tuple(subelement.get("Pos"))
                new_empty.rotation_mode = "QUATERNION"
                new_empty.rotation_quaternion = makeQuatTuple(subelement.get("Rotate"))
                context.scene.collection.objects.link(new_empty)
                new_empty.empty_display_size = 1
                new_empty.empty_display_type = "PLAIN_AXES"

    return {"FINISHED"}


def read_material_from_dae(path):
    ns = {'': 'http://www.collada.org/2005/11/COLLADASchema'}
    try:
        xml_root = ElementTree.parse(path).getroot()
    except:
        print('Unable to open DAE: ', path)
        return None
    return xml_root.find('./asset/extra', ns).get('name')


def add_to_collection(context, name, objs):
    name = name[:61]  # shorten it to max Blender collection name length

    if bpy.data.collections.find(name) != -1:
        new_collection = bpy.data.collections[name]
        new_empty = bpy.data.objects.new("empty", None)
    else:
        new_collection = bpy.data.collections.new(name)
        context.scene.collection.children.link(new_collection)

    viewlayer = context.view_layer.layer_collection.children.get(name)
    if viewlayer:
        context.view_layer.active_layer_collection = viewlayer

    new_empty = bpy.data.objects.get(name) or bpy.data.objects.new("empty", None)
    new_empty.name = name
    try:
        bpy.data.collections[name].objects.link(new_empty)
    except:
        pass

    if type(objs) is list:
        for obj in objs:
            if bpy.data.collections[name].objects.find(obj.name) == -1:
                bpy.data.collections[name].objects.link(obj)
            # context.scene.collection.children.unlink(obj)
            if obj.parent is None:
                obj.parent = new_empty
    else:
        if bpy.data.collections[name].objects.find(objs.name) == -1:
            bpy.data.collections[name].objects.link(objs)
        # context.scene.collection.children.unlink(objs)
        if objs.parent is None:
            objs.parent = new_empty


def set_property(objs, name, value):
    if type(objs) is list:
        for obj in objs:
            obj[name] = value
    else:
        objs[name] = value


def get_root_parent(objs):
    for obj in objs:
        if obj.get("Root") != None:
            return obj
    return None


def import_assets(context, new_assetfilename, option_import=True, option_fixorphans=True):
    new_assetfilename = new_assetfilename.lower()
    bpy.ops.object.select_all(action="DESELECT")
    for obj in context.selected_objects:
        obj.select_set(False)
    # writetoLog("Searching for: " + new_assetfilename)
    new_assets = [
        obj for obj in context.scene.objects if obj.get("Filename") == new_assetfilename
    ]
    # new_assets = []

    if not new_assets:
        if option_import:
            if os.path.isfile(new_assetfilename) is False:
                write_to_logfile("Not found " + new_assetfilename, "Error")
                new_empty = bpy.data.objects.new("empty", None)
                new_empty.empty_display_type = "CUBE"
                return False
            try:
                import_return = bpy.ops.wm.collada_import(filepath=new_assetfilename)
            except:
                write_to_logfile("Import Error " + new_assetfilename, "Error")
                new_empty = bpy.data.objects.new("empty", None)
                new_empty.empty_display_type = "CUBE"
                new_empty["Filename"] = new_assetfilename
                return False

        new_assets = context.selected_objects

        if len(new_assets) == 0:
            write_to_logfile("Nothing created " + new_assetfilename)
            return False
        else:
            write_to_logfile("Imported " + str(len(new_assets)) + " new objects")

        new_assets_parent = [
            obj for obj in new_assets if obj.type == "EMPTY" and "$" not in obj.name
        ]

        for obj in new_assets_parent:
            write_to_logfile("Possible parent " + obj.name)
        for obj in new_assets:
            write_to_logfile("Imported " + str(obj.type) + " " + str(obj.name))
            obj["Filename"] = str(new_assetfilename)
            if option_fixorphans and ".Merged" in obj.name:
                write_to_logfile("Fixing " + obj.name)
                obj.name = Path(new_assetfilename).stem + ".Merged"
                write_to_logfile("Fixed " + obj.name)
                try:
                    obj.parent = new_assets_parent[0]
                    write_to_logfile(
                        "Reparented " + obj.name + " to " + new_assets_parent[0].name
                    )
                except:
                    write_to_logfile("Unable to reparent " + obj.name)
        return True
    else:
        # writetoLog('Duplicating ' + new_assetfilename)
        duped_assetnames = {}
        # bpy.ops.object.select_all(action='DESELECT')
        for obj in new_assets:
            duped_asset = obj.copy()
            context.scene.collection.objects.link(duped_asset)
            duped_asset["Filename"] = ""
            # writetoLog('Duplicated ' + duped_asset.type + ' ' + obj.name + ' -> ' + duped_asset.name)
            duped_assetnames[obj.name] = duped_asset.name
            duped_asset.select_set(True)
        new_assets = bpy.context.selected_objects
        for obj in new_assets:
            if obj.parent:
                obj.parent = get_root_parent(new_assets)
                if obj.parent == None:
                    write_to_logfile(
                        "Unable to reparent "
                        + obj.name
                        + " to asset "
                        + new_assetfilename
                    )
                    return False
                # writetoLog('Reparented ' + obj.name + ' to ' + obj.parent.name)

    return True


def createLightTexture(texture):
    texture = Path(texture).with_suffix('.tif')
    if texture.with_suffix('.png').is_file():
        texture = texture.with_suffix('.png')
    texture_name = Path(texture).stem
    write_to_logfile(f"IES: {texture}")

    if bpy.data.node_groups.get(texture_name):
        return bpy.data.node_groups.get(texture_name)

    new_node = bpy.data.node_groups.new(texture_name, "ShaderNodeTree")
    new_node_output = new_node.nodes.new("NodeGroupOutput")
    new_node.outputs.new("NodeSocketColor", "Color")
    new_node_output.location = (700, 0)
    new_node_texture = new_node.nodes.new("ShaderNodeTexImage")
    new_node_texture.location = (400, 0)
    new_node_texture.location = (400, 0)
    try:
        new_node_texture.image = bpy.data.images.get(
            texture_name
        ) or bpy.data.images.load(str(texture))
        new_node_texture.image.colorspace_settings.name = "Non-Color"
    except:
        write_to_logfile(f"IES not found: {texture}", "Error")
    new_node_mapping = new_node.nodes.new("ShaderNodeMapping")
    new_node_mapping.location = (200, 0)
    new_node_mapping.inputs["Location"].default_value = (0.5, 0.5, 0)
    new_node_mapping.inputs["Scale"].default_value = (0.5, 0.5, 0)
    new_node_texcoord = new_node.nodes.new("ShaderNodeTexCoord")
    new_node_texcoord.location = (0, 0)
    new_node.links.new(
        new_node_texture.outputs["Color"], new_node_output.inputs["Color"]
    )
    new_node.links.new(
        new_node_mapping.outputs["Vector"], new_node_texture.inputs["Vector"]
    )
    new_node.links.new(
        new_node_texcoord.outputs["Normal"], new_node_mapping.inputs["Vector"]
    )

    return new_node


def makeQuatTuple(input):
    output = input.rsplit(",")
    for i in range(0, len(output)):
        output[i] = float(output[i])
        # output[3] *= -1
    output = [output[3], output[2], output[1], output[0]]  # ZYXW to WXYZ
    return output


class ImportSCPrefab(Operator, ImportHelper):
    """ Import an xml from the Prefabs XML from Star Citizen """

    bl_idname = "scdt.import_prefab"  # important since its how bpy.ops.import_test.some_data is constructed
    bl_label = "Import SC Prefab"

    # ImportHelper mixin class uses this
    filename_ext = ".xml"

    filter_glob: StringProperty(
        default="*.xml",
        options={"HIDDEN"},
        maxlen=255,  # Max internal buffer length, longer would be clamped.
    )

    import_data_dir: StringProperty(
        name='Data Dir',
        default='',
        description=(
            "The Data directory containing the assets for the selected Prefab. If blank, this will look for "
            "Data in the parant directories of the Prefab."
        )
    )

    option_brushes: BoolProperty(
        name="option_brushes",
        description="Import Brushes",
        default=True,
    )
    option_component: BoolProperty(
        name="option_component",
        description="Components",
        default=True,
    )
    option_lights: BoolProperty(
        name="option_lights",
        description="Lights",
        default=True,
    )
    option_spawn: BoolProperty(
        name="option_spawn",
        description="Spawn Points",
        default=True,
    )
    option_import: BoolProperty(
        name="option_import",
        description="Import Assets as needed",
        default=True,
    )

    def execute(self, context):
        data_dir = Path(self.import_data_dir) if self.import_data_dir else search_for_data_dir_in_path(self.filepath)

        if not data_dir:
            print(f'Could not determine data directory for prefab')
        elif 'FINISHED' in preimport_prefab(context, self.filepath, data_dir=data_dir):
            if 'FINISHED' in import_cleanup(context):
                return import_prefab(context, self.filepath, data_dir=data_dir)
        return {'CANCELLED'}


def menu_func_import(self, context):
    self.layout.operator(ImportSCPrefab.bl_idname, text="Import SC Prefab")


def register():
    bpy.utils.register_class(ImportSCPrefab)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.utils.unregister_class(ImportSCPrefab)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
