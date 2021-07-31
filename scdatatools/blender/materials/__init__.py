# -*- coding: utf-8 -*-
from pathlib import Path
from datetime import datetime
from ast import literal_eval as make_tuple
from xml.etree import cElementTree as ElementTree

import bpy
import tqdm
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty, BoolProperty, EnumProperty, CollectionProperty
from bpy.types import Operator, OperatorFileListElement

from ..utils import write_to_logfile, search_for_data_dir_in_path

SCSHARDERS_BLEND = Path(__file__).parent / 'SCShaders.blend'
REQUIRED_SHADER_NODE_GROUPS = [
    '.flip normals', '_Glass', '_HardSurface', '_Illum', '_Illum.decal', '_Illum.emit', '_Illum.pom', '_LayerBlend',
    '_LayerMix', '_MaterialLayer', '_Tint', '_Tint.001', 'BlendSeperator', 'Mix channels', 'REEDColors'
]


def ensure_node_groups_loaded() -> bool:
    if not SCSHARDERS_BLEND.is_file():
        return False

    for ng in REQUIRED_SHADER_NODE_GROUPS:
        if ng not in bpy.data.node_groups:
            print(f'Loading SC Shaders node group: {ng}')
            ng_file = SCSHARDERS_BLEND / 'NodeTree' / ng
            try:
                bpy.ops.wm.append(
                    filepath=ng_file.as_posix(),
                    directory=ng_file.parent.as_posix(),
                    filename=ng
                )
            except Exception as e:
                print(f'Failed to load SC Shader "{ng}": {repr(e)}')
    return True


def create_materials_from_mtl(xml_path, data_dir='', use_setting=False):
    if not ensure_node_groups_loaded():
        return False

    xml_path = Path(xml_path)

    if data_dir:
        data_dir = Path(data_dir)
    else:
        # Try to find the Base Dir as a parent of xml_path
        data_dir = search_for_data_dir_in_path(xml_path)
        if not data_dir:
            data_dir = xml_path.parent
            print(f'Could not determine the base Data directory. Defaulting to mtl directory.')

    try:
        parser = ElementTree.XMLParser(encoding='utf-8')
        xml_root = ElementTree.parse(xml_path, parser=parser).iter('Material')
        # xmlRoot = ElementTree.parse(xmlPath).iter('Material')
    except Exception as e:
        write_to_logfile(f"XML not found: {xml_path}", 'Error')
        write_to_logfile(f"Error: {repr(e)}", 'Error')
        return False

    xml_name = xml_path.stem
    write_to_logfile(f"Opening: {xml_path}")

    for element in xml_root:
        if element.get('Name') is None:
            element.set('Name', xml_name)
        write_to_logfile(f"Material Name: {element.get('Name')}")
        write_to_logfile(f"Shader type: {element.get('Shader')}")
        mtlvalues = element.attrib

        for subelement in element:
            # print(" " + subelement.tag)
            mtlvalues[subelement.tag] = subelement
            # for key, value in subelement.attrib.items():
            #     continue
            #    # print("  " + key + ": " + value)
            # for texture in subelement.getchildren():
            # print("  Texture: ")
            # print(texture.attrib)
        if bpy.data.materials.get('Name') and not use_setting:
            if bpy.data.materials['Name']['Filename']:
                write_to_logfile("Skipping")
                continue

        mtlvalues = dict(mtlvalues)
        if element.get('Name') in ("proxy", "Proxy"):
            mat = create_no_surface(mtlvalues)
        elif element.get('Shader') in ("Ilum", "Illum", "MeshDecal"):
            mat = create_ilum_surface(mtlvalues, data_dir=data_dir)
        elif element.get('Shader') == "HardSurface":
            mat = create_hard_surface(mtlvalues, data_dir=data_dir)
        elif element.get('Shader') in ("Glass", "GlassPBR"):
            mat = create_glass_surface(mtlvalues, data_dir=data_dir)
        elif element.get('Shader') == "LayerBlend":
            mat = create_layer_blend_surface(mtlvalues, data_dir=data_dir)
        elif element.get('Shader') == "Layer":
            mat = create_layer_node(mtlvalues, data_dir=data_dir)
        elif element.get('Shader') == "NoDraw":
            mat = create_no_surface(mtlvalues)
        else:
            write_to_logfile(f"Shader type not found {element.get('Shader')}")
            # mat = createUnknownSurface(**mtlvalues)
            continue
        if mat is not None:
            mat['Filename'] = str(xml_path)
            print(f'Imported material {element.get("Name")}')
    return True


def create_ilum_surface(mtl, data_dir):
    write_to_logfile(f'{mtl["Shader"]} - {mtl["SurfaceType"]}')
    mat = (bpy.data.materials.get(mtl["Name"]) or bpy.data.materials.new(mtl["Name"]))

    set_viewport(mat, mtl)

    # Shader
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    shaderout = nodes.new(type="ShaderNodeOutputMaterial")
    shadergroup = nodes.new(type="ShaderNodeGroup")
    write_attribs(mat, mtl, "PublicParams")

    if "pom" in mtl["Name"]:
        shadergroup.node_tree = bpy.data.node_groups['_Illum.pom']
        set_viewport(mat, mtl, True)
    elif "decal" in mtl["Name"]:
        shadergroup.node_tree = bpy.data.node_groups['_Illum.decal']
        set_viewport(mat, mtl, True)
    elif "glow" in mtl["Name"]:
        shadergroup.node_tree = bpy.data.node_groups['_Illum.emit']
    else:
        shadergroup.node_tree = bpy.data.node_groups['_Illum']

    mat.node_tree.links.new(shadergroup.outputs['BSDF'], shaderout.inputs['Surface'])
    mat.node_tree.links.new(shadergroup.outputs['Displacement'], shaderout.inputs['Displacement'])

    if "pom" in mtl["Name"]:
        shadergroup.inputs['Base Color'].default_value = (0.5, 0.5, 0.5, 1)
        shadergroup.inputs['n Strength'].default_value = .1
    else:
        shadergroup.inputs['Base Color'].default_value = mat.diffuse_color

    shadergroup.inputs['ddna Alpha'].default_value = mat.roughness
    shadergroup.inputs['spec Color'].default_value = mat.specular_color[0]

    shaderout.location.x += 200

    load_textures(mtl["Textures"], nodes, mat, data_dir, shadergroup)

    if not mtl.get("MatLayers"):
        return mat

    for submat in mtl["MatLayers"]:
        if "WearLayer" in submat.get("Name"): continue
        path = data_dir / submat.get("Path")
        write_to_logfile(path.stem)
        newbasegroup = nodes.new("ShaderNodeGroup")
        if not create_materials_from_mtl(path, data_dir=data_dir):
            write_to_logfile(f"MTL not found: {path}", "Error")
            continue

        if path.stem in bpy.data.node_groups:
            newbasegroup.node_tree = bpy.data.node_groups[path.stem]
        else:
            write_to_logfile(f'Unknown shader node group: {path.stem}')

        newbasegroup.name = submat.get("Name")
        # newbasegroup.node_tree.label = submat.get("Name")
        newbasegroup.inputs['tint Color'].default_value = make_tuple(str(submat.get("TintColor")) + ",1")
        newbasegroup.inputs['UV Scale'].default_value = [float(submat.get("UVTiling")), float(submat.get("UVTiling")),
                                                         float(submat.get("UVTiling"))]
        newbasegroup.location.x = -600
        newbasegroup.location.y += y
        y -= 260
        mat.node_tree.links.new(newbasegroup.outputs['diff Color'],
                                shadergroup.inputs[newbasegroup.name + ' ' + 'diff Color'])
        mat.node_tree.links.new(newbasegroup.outputs['diff Alpha'],
                                shadergroup.inputs[newbasegroup.name + ' ' + 'diff Alpha'])
        mat.node_tree.links.new(newbasegroup.outputs['ddna Color'],
                                shadergroup.inputs[newbasegroup.name + ' ' + 'ddna Color'])
        mat.node_tree.links.new(newbasegroup.outputs['ddna Alpha'],
                                shadergroup.inputs[newbasegroup.name + ' ' + 'ddna Alpha'])
        mat.node_tree.links.new(newbasegroup.outputs['spec Color'],
                                shadergroup.inputs[newbasegroup.name + ' ' + 'spec Color'])
        mat.node_tree.links.new(newbasegroup.outputs['disp Color'],
                                shadergroup.inputs[newbasegroup.name + ' ' + 'disp Alpha'])
        mat.node_tree.links.new(newbasegroup.outputs['metal Color'],
                                shadergroup.inputs[newbasegroup.name + ' ' + 'metal Alpha'])
        mat.node_tree.links.new(newbasegroup.outputs['blend Color'],
                                shadergroup.inputs[newbasegroup.name + ' ' + 'blend Alpha'])
    return mat


def create_hard_surface(mtl, data_dir):
    write_to_logfile(f'Material: {mtl["Name"]}')
    mat = (bpy.data.materials.get(mtl["Name"]) or bpy.data.materials.new(mtl["Name"]))

    set_viewport(mat, mtl)

    # Shader
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    shaderout = nodes.new(type="ShaderNodeOutputMaterial")
    shadergroup = nodes.new(type="ShaderNodeGroup")
    shadergroup.node_tree = bpy.data.node_groups['_HardSurface']
    mat.node_tree.links.new(shadergroup.outputs['BSDF'], shaderout.inputs['Surface'])
    mat.node_tree.links.new(shadergroup.outputs['Displacement'], shaderout.inputs['Displacement'])
    shadergroup.inputs['Base Color'].default_value = mat.diffuse_color
    shadergroup.inputs['Primary ddna Alpha'].default_value = mat.roughness
    shadergroup.inputs['Metallic'].default_value = 0
    shadergroup.inputs['Anisotropic'].default_value = .5
    shadergroup.inputs['Emission'].default_value = make_tuple(mtl["Emissive"] + ",1")

    shaderout.location.x += 200

    write_attribs(mat, mtl, "PublicParams")
    load_textures(mtl["Textures"], nodes, mat, data_dir, shadergroup)

    if not mtl.get("MatLayers"):
        return mat

    y = -300

    for submat in mtl["MatLayers"]:
        # if "WearLayer" in submat.get("Name"): continue
        path = data_dir / submat.get("Path")
        write_to_logfile(f"MTL: {path}")
        newbasegroup = nodes.new("ShaderNodeGroup")
        if not create_materials_from_mtl(path, data_dir=data_dir):
            write_to_logfile(f"MTL not found: {path}", "Error")
            continue
        if path.stem in bpy.data.node_groups:
            newbasegroup.node_tree = bpy.data.node_groups[path.stem]
        else:
            write_to_logfile(f'Unknown shader node group: {path.stem}')
        if 'Wear' in submat.get("Name"):
            newbasegroup.name = 'Secondary'
        else:
            newbasegroup.name = submat.get("Name")

        # newbasegroup.node_tree.label = submat.get("Name")
        newbasegroup.inputs['tint Color'].default_value = make_tuple(str(submat.get("TintColor")) + ",1")
        newbasegroup.inputs['UV Scale'].default_value = [float(submat.get("UVTiling")), float(submat.get("UVTiling")),
                                                         float(submat.get("UVTiling"))]
        newbasegroup.location.x = -600
        newbasegroup.location.y += y
        y -= 300
        mat.node_tree.links.new(newbasegroup.outputs['diff Color'],
                                shadergroup.inputs[newbasegroup.name + ' ' + 'diff Color'])
        mat.node_tree.links.new(newbasegroup.outputs['diff Alpha'],
                                shadergroup.inputs[newbasegroup.name + ' ' + 'diff Alpha'])
        mat.node_tree.links.new(newbasegroup.outputs['ddna Color'],
                                shadergroup.inputs[newbasegroup.name + ' ' + 'ddna Color'])
        mat.node_tree.links.new(newbasegroup.outputs['ddna Alpha'],
                                shadergroup.inputs[newbasegroup.name + ' ' + 'ddna Alpha'])
        mat.node_tree.links.new(newbasegroup.outputs['spec Color'],
                                shadergroup.inputs[newbasegroup.name + ' ' + 'spec Color'])
        mat.node_tree.links.new(newbasegroup.outputs['disp Color'],
                                shadergroup.inputs[newbasegroup.name + ' ' + 'disp Color'])
        mat.node_tree.links.new(newbasegroup.outputs['blend Color'],
                                shadergroup.inputs[newbasegroup.name + ' ' + 'blend Color'])

    return mat


def create_glass_surface(mtl, data_dir):
    write_to_logfile(f'Material: {mtl["Name"]}')
    mat = (bpy.data.materials.get(mtl["Name"]) or bpy.data.materials.new(mtl["Name"]))

    # Viewport material values
    set_viewport(mat, mtl, True)

    # Shader
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    shaderout = nodes.new(type="ShaderNodeOutputMaterial")
    shadergroup = nodes.new(type="ShaderNodeGroup")
    shadergroup.node_tree = bpy.data.node_groups['_Glass']
    mat.node_tree.links.new(shadergroup.outputs['BSDF'], shaderout.inputs['Surface'])
    mat.node_tree.links.new(shadergroup.outputs['Displacement'], shaderout.inputs['Displacement'])
    shadergroup.inputs['Base Color'].default_value = mat.diffuse_color
    shadergroup.inputs['ddna Alpha'].default_value = mat.roughness
    shadergroup.inputs['spec Color'].default_value = mat.specular_color[0]
    shaderout.location.x += 200

    load_textures(mtl["Textures"], nodes, mat, data_dir, shadergroup)

    return mat


def create_layer_blend_surface(mtl, data_dir):
    write_to_logfile(f'Material: {mtl["Name"]}')
    mat = (bpy.data.materials.get(mtl["Name"]) or bpy.data.materials.new(mtl["Name"]))

    # Viewport material values
    set_viewport(mat, mtl)

    # Shader
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    shaderout = nodes.new(type="ShaderNodeOutputMaterial")
    shadergroup = nodes.new(type="ShaderNodeGroup")
    shadergroup.node_tree = bpy.data.node_groups['_LayerBlend']
    mat.node_tree.links.new(shadergroup.outputs['BSDF'], shaderout.inputs['Surface'])
    mat.node_tree.links.new(shadergroup.outputs['Displacement'], shaderout.inputs['Displacement'])
    shadergroup.inputs['Base Color'].default_value = mat.diffuse_color
    shadergroup.inputs['ddna Alpha'].default_value = mat.roughness
    shaderout.location.x += 200

    # loadMaterials(mtl["MatLayers"])

    load_textures(mtl["Textures"], nodes, mat, data_dir, shadergroup)

    y = -300

    mats = (mtl.get("MatLayers") or mtl.get("MatReferences"))

    if mats is None:
        return

    for submat in mats:
        # if submat.get("Name") in "WearLayer": continue
        path = data_dir / str(submat.get("Path", submat.get("File")))
        write_to_logfile(path.stem)
        newbasegroup = nodes.new("ShaderNodeGroup")
        if not create_materials_from_mtl(path, data_dir=data_dir):
            write_to_logfile(f"MTL not found: {path}", "Error")
            continue
        if path.stem in bpy.data.node_groups:
            newbasegroup.node_tree = bpy.data.node_groups[path.stem]
        else:
            write_to_logfile(f'Unknown shader node group: {path.stem}')
        if submat.get("Name"):
            newbasegroup.name = submat.get("Name")
        elif submat.get("Slot"):
            newbasegroup.name = 'BaseLayer' + str(int(submat.get("Slot")) + 1)
        else:
            newbasegroup.name = 'Unknown'
        # newbasegroup.node_tree.label = submat.get("Name")
        if submat.get("TintColor"):
            newbasegroup.inputs['tint Color'].default_value = make_tuple(str(submat.get("TintColor")) + ",1")
        if submat.get("UVTiling"):
            newbasegroup.inputs['UV Scale'].default_value = [float(submat.get("UVTiling")),
                                                             float(submat.get("UVTiling")),
                                                             float(submat.get("UVTiling"))]

        newbasegroup.location.x = -600
        newbasegroup.location.y += y
        y -= 260
        try:
            mat.node_tree.links.new(newbasegroup.outputs['diff Color'],
                                    shadergroup.inputs[newbasegroup.name + ' ' + 'diff Color'])
            mat.node_tree.links.new(newbasegroup.outputs['diff Alpha'],
                                    shadergroup.inputs[newbasegroup.name + ' ' + 'diff Alpha'])
            mat.node_tree.links.new(newbasegroup.outputs['ddna Color'],
                                    shadergroup.inputs[newbasegroup.name + ' ' + 'ddna Color'])
            mat.node_tree.links.new(newbasegroup.outputs['ddna Alpha'],
                                    shadergroup.inputs[newbasegroup.name + ' ' + 'ddna Alpha'])
            mat.node_tree.links.new(newbasegroup.outputs['spec Color'],
                                    shadergroup.inputs[newbasegroup.name + ' ' + 'spec Color'])
            mat.node_tree.links.new(newbasegroup.outputs['disp Color'],
                                    shadergroup.inputs[newbasegroup.name + ' ' + 'disp Color'])
            mat.node_tree.links.new(newbasegroup.outputs['blend Color'],
                                    shadergroup.inputs[newbasegroup.name + ' ' + 'blend Color'])
            mat.node_tree.links.new(newbasegroup.outputs['metal Color'],
                                    shadergroup.inputs[newbasegroup.name + ' ' + 'metal Color'])
        except:
            write_to_logfile(f"Unable to link layer {newbasegroup.name}")
    return mat


def create_layer_node(mtl, data_dir):
    write_to_logfile(f'Layer node: {mtl["Name"]}')
    if bpy.data.node_groups.get(mtl["Name"]):
        return bpy.data.node_groups.get(mtl["Name"])
    mat = (bpy.data.node_groups.get(mtl["Name"]) or bpy.data.node_groups['_MaterialLayer'].copy())
    mat.name = mtl["Name"]
    nodes = mat.nodes
    load_textures(mtl["Textures"], nodes, mat, data_dir, nodes['Material Output'])
    # manually connect everything for now
    mapnodeout = mat.nodes['Mapping'].outputs['Vector']
    for node in mat.nodes:
        if node.type == 'TEX_IMAGE':
            imagenodein = node.inputs['Vector']
            imagenodecolorout = node.outputs['Color']
            imagenodealphaout = node.outputs['Alpha']
            mat.links.new(imagenodein, mapnodeout)
            if node.name in ['TexSlot12', 'Blendmap']:
                mat.links.new(imagenodecolorout, mat.nodes['Material Output'].inputs['blend Color'])
            elif node.name in ['TexSlot1', '_diff']:
                mat.links.new(imagenodecolorout, mat.nodes['Tint'].inputs['diff Color'])
                mat.links.new(imagenodealphaout, mat.nodes['Tint'].inputs['diff Alpha'])
            elif node.name in ['TexSlot2', '_ddna']:
                mat.links.new(imagenodecolorout, mat.nodes['Material Output'].inputs['ddna Color'])
                mat.links.new(imagenodealphaout, mat.nodes['Material Output'].inputs['ddna Alpha'])
            elif node.name in ['TexSlot4', '_spec']:
                mat.links.new(imagenodecolorout, mat.nodes['Material Output'].inputs['spec Color'])
            elif node.name in ['TexSlot8', 'Heightmap']:
                mat.links.new(imagenodecolorout, mat.nodes['Material Output'].inputs['disp Color'])

    mat['Filename'] = str(mtl['Name'])
    return mat


def create_no_surface(mtl):
    write_to_logfile(f'Material: {mtl["Name"]}')
    mat = (bpy.data.materials.get(mtl["Name"]) or bpy.data.materials.new(mtl["Name"]))
    # Viewport
    mat.blend_method = 'CLIP'
    mat.shadow_method = 'NONE'
    # Shader
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    shaderout = nodes.new(type="ShaderNodeOutputMaterial")
    shadernode = nodes.new('ShaderNodeBsdfTransparent')
    mat.node_tree.links.new(shadernode.outputs['BSDF'], shaderout.inputs['Surface'])
    return mat


def create_attribute(mat, attrs, name):
    return


def load_textures(textures, nodes, mat, data_dir, shadergroup=None):
    imglist = []
    y = 0
    for tex in textures:
        write_to_logfile(f'Texture {tex.attrib} {tex.get("File")}')
        path = data_dir / tex.get("File")
        if path.with_suffix('.png').is_file():
            path = path.with_suffix('.png')
        elif path.with_suffix('.tif').is_file():
            path = path.with_suffix('.tif')
        elif path.with_suffix('.tga').is_file():
            path = path.with_suffix('.tga')

        try:
            img = (bpy.data.images.get(tex.get("File")) or bpy.data.images.load(str(path)))
        except:
            write_to_logfile(f"Texture not found: {path}", "Error")
            write_to_logfile(path, "Missing Textures")
            continue
        if 'diff' in img.name:
            img.colorspace_settings.name = 'sRGB'
        else:
            img.colorspace_settings.name = 'sRGB'

        img.alpha_mode = 'PREMUL'
        texnode = (nodes.get(img.name) or nodes.new(type='ShaderNodeTexImage'))
        texnode.image = img
        texnode.label = img.name
        texnode.name = tex.get("Map")

        texnode.location.x -= 300
        texnode.location.y = y
        y -= 330

        if list(tex):
            texmod = tex[0]
            write_to_logfile("Texture mod found", 'Debug')
            mapnode = nodes.new(type='ShaderNodeMapping')
            if texmod.get('TileU') and texmod.get('TileV'):
                mapnode.inputs['Scale'].default_value = (float(texmod.get('TileU')), float(texmod.get('TileV')), 1)
                if mapnode.inputs['Scale'].default_value == [0, 0, 1]:
                    mapnode.inputs['Scale'].default_value = [1, 1, 1]
                try:
                    mat.node_tree.links.new(mapnode.outputs['Vector'], texnode.inputs['Vector'])
                except:
                    pass
            mapnode.location = texnode.location
            mapnode.location.x -= 300
            # mat.node_tree.links.new(mapnode.outputs['Vector'], texnode.inputs['Vector'])

        if not hasattr(mat, 'node_tree'):
            write_to_logfile("Shader node tree doesn't exist")
            continue

            # link everything up
        if tex.get("Map") in ['TexSlot1', 'Diffuse']:
            texnode.image.colorspace_settings.name = 'sRGB'
            try:
                mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['diff Color'])
                mat.node_tree.links.new(texnode.outputs['Alpha'], shadergroup.inputs['diff Alpha'])
            except:
                try:
                    mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['Primary diff Color'])
                    mat.node_tree.links.new(texnode.outputs['Alpha'], shadergroup.inputs['Primary diff Alpha'])
                except:
                    write_to_logfile("Failed to link Diffuse Map")
        elif tex.get("Map") in ['TexSlot2', 'Bumpmap']:
            try:
                mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['ddna Color'])
                # mat.node_tree.links.new(texnode.outputs['Alpha'], shadergroup.inputs['ddna Alpha'])
            except:
                mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['Primary ddna Color'])
                continue
        elif tex.get("Map") in ['TexSlot3']:
            try:
                # mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['ddna Color'])
                mat.node_tree.links.new(texnode.outputs['Alpha'], shadergroup.inputs['ddna Alpha'])
            except:
                try:
                    mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['Primary ddna Color'])
                    mat.node_tree.links.new(texnode.outputs['Alpha'], shadergroup.inputs['Primary ddna Alpha'])
                except:
                    write_to_logfile("Failed to link DDNA Map")
        elif tex.get("Map") in ['TexSlot4', 'Specular']:
            mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['spec Color'])
        elif tex.get("Map") in ['TexSlot6']:
            try:
                mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['detail Color'])
                mat.node_tree.links.new(texnode.outputs['Alpha'], shadergroup.inputs['detail Alpha'])
            except:
                write_to_logfile("Failed to link detail Map")
                continue
        elif tex.get("Map") in ['TexSlot8', 'Heightmap']:
            try:
                mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['disp Color'])
            except:
                pass
        elif tex.get("Map") in ['TexSlot9', 'Decalmap']:
            try:
                mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['decal Color'])
                mat.node_tree.links.new(texnode.outputs['Alpha'], shadergroup.inputs['decal Alpha'])
            except:
                write_to_logfile("Failed to link Decal Map")
                continue
        elif tex.get("Map") in ['TexSlot11', 'WDA']:
            try:
                mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['wda Color'])
                mat.node_tree.links.new(texnode.outputs['Alpha'], shadergroup.inputs['wda Alpha'])
            except:
                write_to_logfile("Failed to link WDA Map")
                continue
        elif tex.get("Map") in ['TexSlot12', 'Blendmap']:
            try:
                mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['blend Color'])
            except:
                write_to_logfile("Failed to link Blend Map")
                continue
        elif tex.get("Map") in ['TexSlot13', 'Blendmap']:
            try:
                # mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['detail Color'])
                # mat.node_tree.links.new(texnode.outputs['Alpha'], shadergroup.inputs['detail Alpha'])
                pass
            except:
                write_to_logfile("Failed to link detail Map")
                continue

    return mat


def load_materials(materials, data_dir, use_setting=False):
    for mat in tqdm.tqdm(materials, desc='Loading materials'):
        if not mat:
            continue
        if not Path(mat).is_file():
            mat = data_dir / mat
            if not mat.is_file():
                write_to_logfile(f'Could not find mtl file: {mat}', 'Error')
                continue
        write_to_logfile(f"Path: {mat}")
        create_materials_from_mtl(mat, data_dir=data_dir, use_setting=use_setting)


def set_viewport(mat, mtl, trans=False):
    # Viewport material values
    mat.diffuse_color = make_tuple(mtl["Diffuse"] + ",1")
    # mat.specular_color = make_tuple(mtl["Specular"],.5)
    # mat.roughness = 1-(float(mtl["Shininess"].5)/255)
    if trans:
        mat.blend_method = 'BLEND'
        mat.shadow_method = 'NONE'
        mat.show_transparent_back = True
        mat.cycles.use_transparent_shadow = True
        mat.use_screen_refraction = True
        mat.refraction_depth = .0001
    else:
        mat.blend_method = 'OPAQUE'
        mat.shadow_method = 'CLIP'
        mat.cycles.use_transparent_shadow = False
        mat.show_transparent_back = False
    return


def write_attribs(mat, mtl, attr):
    # if not mtl.get(attr): return False
    for name, value in mtl[attr].attrib.items():
        write_to_logfile(f"{name} {value}", 'Debug')
        mat[name] = value
        if mat.node_tree.nodes['Group'].inputs.get(name):
            mat.node_tree.nodes['Group'].inputs[name].default_value = float(value)
    return


class LoadSCShaderNodes(Operator):
    """ Load the SC Shader nodes if not already loaded """
    bl_idname = "scdt.load_sc_shader_nodes"
    bl_label = "Load SC Shader Nodes"

    def execute(self, context):
        if ensure_node_groups_loaded():
            return {'FINISHED'}
        return {'CANCELLED'}


class ImportSCMTL(Operator, ImportHelper):
    """ Imports Star Citizen Material file and textures """
    bl_idname = "scdt.import_material"
    bl_label = "Import SC Materials"

    files: CollectionProperty(
        name="File Path",
        type=bpy.types.PropertyGroup,
    )

    # ImportHelper mixin class uses this
    filename_ext = ".mtl"

    filter_glob: StringProperty(
        default="*.mtl",
        options={'HIDDEN'},
        maxlen=255,  # Max internal buffer length, longer would be clamped.
    )

    import_data_dir: StringProperty(
        default='',
    )

    # List of operator properties, the attributes will be assigned
    # to the class instance from the operator settings before calling.
    use_setting: BoolProperty(
        name="Overwrite Materials",
        description="Overwrite materials that have the same name (UNIMPLMENTED)",
        default=True,
    )

    def execute(self, context):
        dirpath = Path(self.filepath)
        if dirpath.is_file():
            dirpath = dirpath.parent

        load_materials([dirpath / _.name for _ in self.files],
                       data_dir=self.import_data_dir, use_setting=self.use_setting)

        return {'FINISHED'}


# Only needed if you want to add into a dynamic menu
def menu_func_import(self, context):
    self.layout.operator(ImportSCMTL.bl_idname, text="Import SC Materials")


def register():
    bpy.utils.register_class(LoadSCShaderNodes)
    bpy.utils.register_class(ImportSCMTL)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.utils.unregister_class(LoadSCShaderNodes)
    bpy.utils.unregister_class(ImportSCMTL)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
