# -*- coding: utf-8 -*-
import os
import typing
import logging
import xml.etree
from distutils.util import strtobool

from copy import copy, deepcopy
from pathlib import Path
from ast import literal_eval as make_tuple
from xml.etree import cElementTree as ElementTree

import bpy
import tqdm
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty, BoolProperty, CollectionProperty

from scdatatools.engine.cryxml import is_cryxmlb_file, etree_from_cryxml_file
from scdatatools.utils import search_for_data_dir_in_path

from .utils import ensure_node_groups_loaded, image_for_texture

logger = logging.getLogger(__name__)

POSSIBLE_NODE_CONNECTIONS = [
    'diff Color', 'diff Alpha', 'ddna Color', 'ddna Alpha', 'spec Color', 'disp Color', 'metal Color', 'blend Color'
]

# Key names of the ShaderNode__ node groups
SN_OUT = 'Material Output'
SN_GROUP = 'Group'


def _link_possible_node_connections(mat, output_node, input_node, input_prefix=''):
    if input_prefix:
        input_prefix = input_prefix.strip() + ' '
    for pn in POSSIBLE_NODE_CONNECTIONS:
        in_name = f'{input_prefix}{pn}'
        if pn in output_node.outputs and in_name in input_node.inputs:
            mat.node_tree.links.new(output_node.outputs[pn], input_node.inputs[in_name])


def set_viewport(mat, mtl_attrs, trans=False):
    # Viewport material values
    mat.diffuse_color = make_tuple(mtl_attrs.get("Diffuse", '1,1,1') +',1')    
    mat.roughness = 1-(float(mtl_attrs.get("Shininess", 128))/255)
    if trans:
        mat.blend_method = 'BLEND'
        mat.shadow_method = 'NONE'
        mat.show_transparent_back = True
        mat.cycles.use_transparent_shadow = True
        mat.use_screen_refraction = True
        mat.refraction_depth = .0005
    else:
        mat.blend_method = 'OPAQUE'
        mat.shadow_method = 'CLIP'
        mat.cycles.use_transparent_shadow = False
        mat.show_transparent_back = False


def write_attrib_to_mat(mat, mtl_attrs, attr):
    for name, value in mtl_attrs[attr].attrib.items():
        mat[name] = value
        if SN_GROUP in mat.node_tree.nodes and mat.node_tree.nodes[SN_GROUP].inputs.get(name):
            mat.node_tree.nodes[SN_GROUP].inputs[name].default_value = float(value)


def a_to_c(attrs, alpha=1.0):
    """ Convert `attrs` dict with `r`, `g`, and `b` to a color tuple (r,g,b,alpha)
    :param attrs: dict with rgb values in seperate keys
    :param alpha: alpha value for new color
    :returns: `tuple` of the converted (r,g,b,alpha) values
    """
    r = color_srgb_to_scene_linear(float(attrs['r']) / 256)
    g = color_srgb_to_scene_linear(float(attrs['g']) / 256)
    b = color_srgb_to_scene_linear(float(attrs['b']) / 256)
    return r, g, b, alpha


def getsRGBColor(c):
    r, g, b, a = make_tuple(c)
    x = color_srgb_to_scene_linear(r)
    y = color_srgb_to_scene_linear(g)
    z = color_srgb_to_scene_linear(b)
    return x, y, z, a


def color_srgb_to_scene_linear(c):
    # c *= 2.4 
    if c < 0.04045:
        return 0.0 if c < 0.0 else c * (1.0 / 12.92)
    else:
        return ((c + 0.055) * (1.0 / 1.055)) ** 2.4


class MTLLoader:
    def __init__(self, data_dir='', tint_palette_node_group=None):
        """
        :param data_dir: A data directory that is the root of exported assets that are referenced from the materials. By
            default this will be automatically determined from the `mtl` path, or use the `mtl` parent directory as a
            last resort
        :param tint_palette_node_group: tint_palette_node_group to use for these materials
        """
        if not ensure_node_groups_loaded():
            raise RuntimeError(f'could not load SC shader node groups')

        self.tint_palette_node_group = tint_palette_node_group
        self.data_dir = Path(data_dir) if data_dir else data_dir
        self.created_materials = []
        self.tinted_materials = []

    def _load_sub_material(self, mtl_path: typing.Union[Path, str]) -> typing.Union[None, Path]:
        """
        Load the sub-material from the path `mtl_path`

        :params mtl_path: Path to the submaterial relative to the set `data_dir`
        :retuns: `None` if the sub material could not be loaded or the `Path` of the mtl that was loaded from disk
        """
        if not mtl_path:
            return

        path = self.data_dir / mtl_path
        if not path.is_file():
            logger.error('could not find sub-material file "%s"', path)
            return

        try:
            self.create_materials_from_mtl(path)
        except Exception as e:
            logger.exception('error parsing sub-material', exc_info=e)
            return
        return path

    def get_or_create_shader_material(self, name) -> (object, bool):
        """ Returns the Material `name` and whether or not the `Material` has been initialized (if `filename`
        attribute has been set. If the material does not exist at all, it will create it and setup the base node groups
        for a sc shader material
        """
        if name in bpy.data.materials:
            mat = bpy.data.materials[name]
            if mat.get('filename'):
                return mat, False
        else:
            mat = bpy.data.materials.new(name)

        # Shader
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        nodes.clear()
        nodes.new(type="ShaderNodeOutputMaterial")
        nodes.new(type="ShaderNodeGroup")
        return mat, True

    def create_materials_from_mtl(self, mtl: typing.Union[str, Path, typing.IO], _sub_mat: bool = False) -> list:
        """
        Parses and creates all missing materials defined in a StarCitizen `mtl` file.

        :param mtl: The mtl path or `file`.
        :param _sub_mat: Reserved for use when loading a sub-material
        :return: `list` of the materials that were newly created
        """
        if isinstance(mtl, (str, Path)):
            mtl_path = Path(mtl)
            mtl = mtl_path.open('rb')
        else:
            mtl_path = Path(mtl.name)

        if not self.data_dir:
            # Try to find the Base Dir as a parent of xml_path
            self.data_dir = search_for_data_dir_in_path(mtl_path)
            if not self.data_dir:
                self.data_dir = mtl_path.parent
                logger.debugscbp(f'could not determine data_dir from mtl path. defaulting to mtl directory %s',
                                 self.data_dir)

        try:
            if is_cryxmlb_file(mtl):
                et = etree_from_cryxml_file(mtl)
            else:
                et = ElementTree.parse(mtl, parser=ElementTree.XMLParser(encoding='utf-8'))
        except Exception as e:
            logger.error(f"could not load material {mtl_path}", exc_info=e)
            raise

        logger.debugscbp(f"Parsing mtl %s", mtl_path)

        for mat in et.findall('.//Material') + [et.getroot()]:
            attrs = copy(mat.attrib)

            if 'Name' not in attrs:
                attrs['Name'] = mtl_path.stem

            for subelement in mat:
                attrs[subelement.tag] = subelement

            # Convert the name to match the exported prefixed-material names
            attrs['Name'] = f'{mtl_path.name.lower().replace(".", "_")}_{attrs["Name"]}'

            shader_type = attrs.get('Shader', '').lower()
            # shader_type = "simple"

            try:
                new_mat = None
                if attrs['Name'].endswith("proxy"):
                    new_mat = self.create_proxy_material(attrs)
                elif shader_type == "hardsurface":
                    new_mat = self.create_hard_surface(attrs)
                elif shader_type in ("illum", "meshdecal", "decal"):
                    new_mat = self.create_illum_surface(attrs)
                elif shader_type in ("glass", "glasspbr"):
                    new_mat = self.create_glass_surface(attrs)
                elif shader_type == "layerblend":
                    new_mat = self.create_layer_blend_surface(attrs)
                elif shader_type == "layer":
                    new_mat = self.create_layer_node(attrs)
                elif shader_type in ("hologram", "hologramcig"):
                    new_mat = self.create_hologram_surface(attrs)
                elif shader_type in ("monitor", "displayscreen", "uiplane"):
                    new_mat = self.create_proxy_material(attrs)
                elif shader_type == "nodraw":
                    new_mat = self.create_proxy_material(attrs)
                elif shader_type == "simple":
                    new_mat = self.create_simple_material(attrs)
                else:
                    if shader_type:
                        logger.warning("could not create %s, shader type '%s' unknown", attrs['Name'], shader_type)
                    continue
                if new_mat is not None:
                    logger.debugscbp("created %s mtl %s", shader_type, attrs['Name'])
                    new_mat['filename'] = mtl_path.as_posix()
                    new_mat['SurfaceType'] = attrs.get('SurfaceType', "None")
                    new_mat['StringGenMask'] = attrs.get('StringGenMask')
                    self.created_materials.append(new_mat)
            except Exception as e:
                logger.exception(f'Error creating material {attrs["Name"]}', exc_info=e)
        return self.created_materials

    def create_illum_surface(self, mtl_attrs):
        """ Setup a material as an _Illum shader from `mtl_attrs` dict definition """
        mat, created = self.get_or_create_shader_material(mtl_attrs["Name"])
        if not created:
            return

        shaderout = mat.node_tree.nodes[SN_OUT]
        shadergroup = mat.node_tree.nodes[SN_GROUP]
        matname = mtl_attrs.get("Name")
        write_attrib_to_mat(mat, mtl_attrs, "PublicParams")

        viewport_trans = False
        if "pom" in matname.lower():
            shadergroup.node_tree = bpy.data.node_groups['_Illum.pom']
            viewport_trans = True
        elif "glow" in matname.lower() and "link" in matname.lower():  #
            shadergroup.node_tree = bpy.data.node_groups['_Illum.emit']
            shadergroup.inputs['emit Strength'].default_value = 4
            if "unlink" in matname.lower():
                shadergroup.inputs['geom link'].default_value = 0
            else:
                shadergroup.inputs['geom link'].default_value = 1
            shadergroup.node_tree = bpy.data.node_groups['_Illum.emit']
            shadergroup.inputs['emit Strength'].default_value = 4
        elif "DECAL" in mtl_attrs["StringGenMask"]:
            shadergroup.node_tree = bpy.data.node_groups['_Illum.decal']
            viewport_trans = True
        else:
            shadergroup.node_tree = bpy.data.node_groups['_Illum']
        set_viewport(mat, mtl_attrs, viewport_trans)

        mat.node_tree.links.new(shadergroup.outputs['BSDF'], shaderout.inputs['Surface'])
        mat.node_tree.links.new(shadergroup.outputs['Displacement'], shaderout.inputs['Displacement'])

        if "pom" in matname.lower():
            shadergroup.inputs['Base Color'].default_value = (0.18, 0.18, 0.18, 1)
            shadergroup.inputs['n Strength'].default_value = 10
            shadergroup.inputs['Metallic'].default_value = 1
        else:
            shadergroup.inputs['Base Color'].default_value = make_tuple(mtl_attrs.get("Diffuse") + ",1")

        # shadergroup.inputs['ddna Alpha'].default_value = mat.roughness

        try:
            shadergroup.inputs['spec Color'].default_value = make_tuple(mtl_attrs.get("Specular") + ",1")
        except:            
            pass

        try:
            shadergroup.inputs['Glow'].default_value = float(mtl_attrs.get('Glow', 0)) * pow(2, 6)
        except:
            pass

        try:
            shadergroup.inputs['BlendLayer2DiffuseColor'].default_value = make_tuple(mtl_attrs["PublicParams"].get("BlendLayer2DiffuseColor", "1,1,1") + ",1")            
        except:            
            pass
        try:
            shadergroup.inputs['BlendLayer2SpecularColor'].default_value = make_tuple(mtl_attrs["PublicParams"].get("BlendLayer2SpecularColor", "0.5,0.5,0.5") + ",1")
        except:            
            pass
        try:
            shadergroup.inputs['BlendLayer2Glossiness'].default_value = float(mtl_attrs["PublicParams"].get("BlendLayer2Glossiness", 128))/255            
        except: 
            pass

        if mtl_attrs.get('AlphaTest'):
            try:
                shadergroup.inputs['UseAlpha'].default_value = 1
            except:
                pass

        if "USE_SPECULAR_MAPS" in mtl_attrs["StringGenMask"]:
            shadergroup.inputs['Metallic'].default_value = 1
            shadergroup.inputs['Anisotropic'].default_value = .5
        else:
            shadergroup.inputs['Metallic'].default_value = 0
            shadergroup.inputs['Anisotropic'].default_value = 0

        #if "USE_OPACITY_MAP" in mtl_attrs["StringGenMask"]:
        #    shadergroup.inputs['UseAlpha'].default_value = 1

        shaderout.location.x += 200

        self.load_textures(mtl_attrs.get("Textures", []), mat, shadergroup)

        y = 0
        nodes = mat.node_tree.nodes
        for submat in mtl_attrs.get("MatLayers", []):
            if "WearLayer" in submat.get("Name"):
                continue
            if (subpath := self._load_sub_material(submat.get("Path"))) is None:
                continue

            newbasegroup = nodes.new("ShaderNodeGroup")
            if subpath.stem in bpy.data.node_groups:
                newbasegroup.node_tree = bpy.data.node_groups[subpath.stem]
            else:
                logger.error(f'Unknown shader node group "{subpath.stem}" in {mat.name}')
                continue

            newbasegroup.name = submat.get("Name")
            # newbasegroup.node_tree.label = submat.get("Name")
            newbasegroup.inputs['tint Color'].default_value = make_tuple(submat.get("TintColor") + ",1")
            newbasegroup.inputs['UV Scale'].default_value = [float(submat.get("UVTiling")),
                                                             float(submat.get("UVTiling")),
                                                             float(submat.get("UVTiling"))]
            newbasegroup.location.x = -600
            newbasegroup.location.y += y
            y -= 260
            _link_possible_node_connections(mat, newbasegroup, shadergroup, newbasegroup.name)
        return mat

    def create_hard_surface(self, mtl_attrs):
        """ Setup a material as an hard shader from `mtl_attrs` dict definition """
        mat_name = mtl_attrs['Name']

        use_tint_node_group = False
        for submat in mtl_attrs["MatLayers"]:
            if submat.get("PaletteTint", "0") != "0":
                use_tint_node_group = True

        # use_tint_node_group = (
        # self.tint_palette_node_group is not None and 'notint' not in mat_name.lower()
        # and not mat_name.lower().endswith('_b')
        # and any(_ in mat_name.lower() for _ in ('_primary', '_secondary', '_tertiary'))
        # )

        # if use_tint_node_group:
        #     mat_name = f'{mat_name}_{self.tint_palette_node_group}'

        mat, created = self.get_or_create_shader_material(mat_name)
        if not created:
            return  # mtl has already been loaded

        set_viewport(mat, mtl_attrs)

        nodes = mat.node_tree.nodes
        shaderout = mat.node_tree.nodes[SN_OUT]
        shadergroup = mat.node_tree.nodes[SN_GROUP]
        shadergroup.node_tree = bpy.data.node_groups['_HardSurface']
        mat.node_tree.links.new(shadergroup.outputs['BSDF'], shaderout.inputs['Surface'])
        mat.node_tree.links.new(shadergroup.outputs['Displacement'], shaderout.inputs['Displacement'])
        shadergroup.inputs['Base Color'].default_value = mat.diffuse_color
        shadergroup.inputs['Primary ddna Alpha'].default_value = mat.roughness
        if "USE_SPECULAR_MAPS" in mtl_attrs["StringGenMask"]:
            shadergroup.inputs['Metallic'].default_value = 1
            shadergroup.inputs['Anisotropic'].default_value = .5
        else:
            shadergroup.inputs['Metallic'].default_value = 0
            shadergroup.inputs['Anisotropic'].default_value = 0
        shadergroup.inputs['Emission'].default_value = make_tuple(mtl_attrs["Emissive"] + ",1")
        shaderout.location.x += 200

        tint_group = None
        if use_tint_node_group:
            self.tinted_materials.append(mat)
            tint_group = nodes.new("ShaderNodeGroup")
            tint_group.node_tree = self.tint_palette_node_group
            tint_group.location.x = -800

        write_attrib_to_mat(mat, mtl_attrs, "PublicParams")
        self.load_textures(mtl_attrs["Textures"], mat, shadergroup)

        if not mtl_attrs.get("MatLayers"):
            return mat

        y = -300

        for submat in mtl_attrs["MatLayers"]:
            if (subpath := self._load_sub_material(submat.get("Path"))) is None:
                continue

            if subpath.stem in bpy.data.node_groups:
                newbasegroup = nodes.new("ShaderNodeGroup")
                newbasegroup.node_tree = bpy.data.node_groups[subpath.stem]
            else:
                logger.error(f'Unknown shader node group "{subpath.stem}" in {mat.name}')
                continue

            if 'Wear' in submat.get("Name"):
                newbasegroup.name = 'Secondary'
            else:
                newbasegroup.name = submat.get("Name")

            # newbasegroup.node_tree.label = submat.get("Name")
            newbasegroup.inputs['tint diff Color'].default_value = make_tuple(submat.get("TintColor") + ",1")
            newbasegroup.inputs['UV Scale'].default_value = [float(submat.get("UVTiling")),
                                                             float(submat.get("UVTiling")),
                                                             float(submat.get("UVTiling"))]
            newbasegroup.location.x = -600
            newbasegroup.location.y += y
            y -= 300
            _link_possible_node_connections(mat, newbasegroup, shadergroup, newbasegroup.name)
            if tint_group is not None:
                if submat.get("PaletteTint", "0") == "1":
                    mat.node_tree.links.new(tint_group.outputs['Primary'], newbasegroup.inputs['tint diff Color'])
                    mat.node_tree.links.new(tint_group.outputs['Primary SpecColor'],
                                            newbasegroup.inputs['tint spec Color'])
                    mat.node_tree.links.new(tint_group.outputs['Primary Glossiness'],
                                            newbasegroup.inputs['tint gloss'])                                            
                elif submat.get("PaletteTint", "0") == "2":
                    mat.node_tree.links.new(tint_group.outputs['Secondary'], newbasegroup.inputs['tint diff Color'])
                    mat.node_tree.links.new(tint_group.outputs['Secondary SpecColor'],
                                            newbasegroup.inputs['tint spec Color'])
                    mat.node_tree.links.new(tint_group.outputs['Secondary Glossiness'],
                                            newbasegroup.inputs['tint gloss'])
                elif submat.get("PaletteTint", "0") == "3":
                    mat.node_tree.links.new(tint_group.outputs['Tertiary'], newbasegroup.inputs['tint diff Color'])
                    mat.node_tree.links.new(tint_group.outputs['Tertiary SpecColor'],
                                            newbasegroup.inputs['tint spec Color'])
                    mat.node_tree.links.new(tint_group.outputs['Tertiary Glossiness'],
                                            newbasegroup.inputs['tint gloss'])

        return mat

    def create_glass_surface(self, mtl_attrs):
        mat, created = self.get_or_create_shader_material(mtl_attrs["Name"])
        if not created:
            return

        nodes = mat.node_tree.nodes
        shaderout = mat.node_tree.nodes[SN_OUT]
        shadergroup = mat.node_tree.nodes[SN_GROUP]

        # Viewport material values
        set_viewport(mat, mtl_attrs, True)

        shadergroup.node_tree = bpy.data.node_groups['_Glass']
        mat.node_tree.links.new(shadergroup.outputs['BSDF'], shaderout.inputs['Surface'])
        mat.node_tree.links.new(shadergroup.outputs['Displacement'], shaderout.inputs['Displacement'])
        shadergroup.inputs['Base Color'].default_value = mat.diffuse_color
        # shadergroup.inputs['ddna Alpha'].default_value = mat.roughness
        # shadergroup.inputs['spec Color'].default_value = mat.specular_color[0]/2
        shadergroup.inputs['IOR'].default_value = 1.45
        shaderout.location.x += 200

        self.load_textures(mtl_attrs["Textures"], mat, shadergroup)

        return mat

    def create_layer_blend_surface(self, mtl_attrs):
        mat, created = self.get_or_create_shader_material(mtl_attrs["Name"])
        if not created:
            return

        nodes = mat.node_tree.nodes
        shaderout = mat.node_tree.nodes[SN_OUT]
        shadergroup = mat.node_tree.nodes[SN_GROUP]

        # Viewport material values
        set_viewport(mat, mtl_attrs)

        shadergroup.node_tree = bpy.data.node_groups['_LayerBlend']
        mat.node_tree.links.new(shadergroup.outputs['BSDF'], shaderout.inputs['Surface'])
        mat.node_tree.links.new(shadergroup.outputs['Displacement'], shaderout.inputs['Displacement'])
        shadergroup.inputs['Base Color'].default_value = mat.diffuse_color
        shadergroup.inputs['ddna Alpha'].default_value = mat.roughness
        shaderout.location.x += 200

        use_tint_node_group = False
        for submat in mtl_attrs.get("MatLayers", []):
            if submat.get("PaletteTint", "0") != "0":
                use_tint_node_group = True
        
        tint_group = None
        if use_tint_node_group:
            self.tinted_materials.append(mat)
            tint_group = nodes.new("ShaderNodeGroup")
            tint_group.node_tree = self.tint_palette_node_group
            tint_group.location.x = -600

        # loadMaterials(mtl["MatLayers"])

        self.load_textures(mtl_attrs["Textures"], mat, shadergroup)

        y = -300

        mats = (mtl_attrs.get("MatLayers") or mtl_attrs.get("MatReferences"))

        if mats is None:
            return

        for submat in mats:
            if (subpath := self._load_sub_material(submat.get("Path"))) is None:
                continue

            newbasegroup = nodes.new("ShaderNodeGroup")
            if subpath.stem in bpy.data.node_groups:
                newbasegroup.node_tree = bpy.data.node_groups[subpath.stem]
            else:
                logger.error(f'Unknown shader node group "{subpath.stem}" in {mat.name}')
                continue
            if submat.get("Name"):
                newbasegroup.name = submat.get("Name")
            elif submat.get("Slot"):
                newbasegroup.name = 'BaseLayer' + str(int(submat.get("Slot")) + 1)
            else:
                newbasegroup.name = 'Unknown'
            # newbasegroup.node_tree.label = submat.get("Name")
            if submat.get("TintColor"):
                newbasegroup.inputs['tint diff Color'].default_value = make_tuple(submat.get("TintColor") + ",1")
            if submat.get("UVTiling"):
                newbasegroup.inputs['UV Scale'].default_value = [float(submat.get("UVTiling")),
                                                                 float(submat.get("UVTiling")),
                                                                 float(submat.get("UVTiling"))]
            if tint_group is not None:
                if submat.get("PaletteTint", "0") == "1":
                    mat.node_tree.links.new(tint_group.outputs['Primary'], newbasegroup.inputs['tint diff Color'])
                    mat.node_tree.links.new(tint_group.outputs['Primary SpecColor'],
                                            newbasegroup.inputs['tint spec Color'])
                    mat.node_tree.links.new(tint_group.outputs['Primary Glossiness'],
                                            newbasegroup.inputs['tint gloss'])                                            
                elif submat.get("PaletteTint", "0") == "2":
                    mat.node_tree.links.new(tint_group.outputs['Secondary'], newbasegroup.inputs['tint diff Color'])
                    mat.node_tree.links.new(tint_group.outputs['Secondary SpecColor'],
                                            newbasegroup.inputs['tint spec Color'])
                    mat.node_tree.links.new(tint_group.outputs['Secondary Glossiness'],
                                            newbasegroup.inputs['tint gloss'])
                elif submat.get("PaletteTint", "0") == "3":
                    mat.node_tree.links.new(tint_group.outputs['Tertiary'], newbasegroup.inputs['tint diff Color'])
                    mat.node_tree.links.new(tint_group.outputs['Tertiary SpecColor'],
                                            newbasegroup.inputs['tint spec Color'])
                    mat.node_tree.links.new(tint_group.outputs['Tertiary Glossiness'],
                                            newbasegroup.inputs['tint gloss'])

            newbasegroup.location.x = -600
            newbasegroup.location.y += y
            y -= 300
            _link_possible_node_connections(mat, newbasegroup, shadergroup, newbasegroup.name)
        return mat

    def create_layer_node(self, mtl_attrs):
        layer_node_name = mtl_attrs["Name"].split('_mtl_')[-1]
        logger.debugscbp(f'Layer node: {layer_node_name}')
        if bpy.data.node_groups.get(layer_node_name):
            return bpy.data.node_groups[layer_node_name]
        mat = (bpy.data.node_groups.get(layer_node_name) or bpy.data.node_groups['_MaterialLayer'].copy())
        mat.name = layer_node_name
        nodes = mat.nodes

        #mat['filename'] = mtl_path.as_posix()

        mat.nodes["Tint"].inputs['diff Color'].default_value = make_tuple(mtl_attrs.get("Diffuse") + ",1")
        mat.nodes["Tint"].inputs['spec Color'].default_value = make_tuple(mtl_attrs.get("Specular") + ",1")
        mat.nodes["detail Scale"].outputs[0].default_value = float(mtl_attrs["PublicParams"].get("DetailTiling", 1))

        self.load_textures(mtl_attrs["Textures"], mat, nodes[SN_OUT])

        # manually connect everything for now
        mapnodeout = mat.nodes['Mapping'].outputs['Vector']
        detailmapnodeout = mat.nodes['detail Mapping'].outputs['Vector']
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
                elif node.name in ['TexSlot2']:
                    mat.links.new(imagenodecolorout, mat.nodes['Tint'].inputs['ddna Color'])                    
                    mat.links.new(imagenodealphaout, mat.nodes['Tint'].inputs['ddna Alpha'])
                elif node.name in ['TexSlot3', 'TexSlot2A']:
                    mat.links.new(imagenodecolorout, mat.nodes['Tint'].inputs['ddna Alpha'])                    
                elif node.name in ['TexSlot6', '_spec']:
                    mat.links.new(imagenodecolorout, mat.nodes['Tint'].inputs['spec Color'])
                elif node.name in ['TexSlot7', 'detail']:
                    mat.links.new(imagenodecolorout, mat.nodes['Tint'].inputs['detail Color'])
                    mat.links.new(imagenodealphaout, mat.nodes['Tint'].inputs['detail Alpha'])
                    mat.links.new(imagenodein, detailmapnodeout)
                elif node.name in ['TexSlot8', 'Heightmap']:
                    mat.links.new(imagenodecolorout, mat.nodes['Material Output'].inputs['disp Color'])
                elif node.name in ['TexSlot9', 'Blendmap']:
                    mat.links.new(imagenodecolorout, mat.nodes['Material Output'].inputs['blend Color'])
        return mat

    def create_hologram_surface(self, mtl_attrs):
        mat, created = self.get_or_create_shader_material(mtl_attrs["Name"])
        if not created:
            return

        shaderout = mat.node_tree.nodes[SN_OUT]
        shadergroup = mat.node_tree.nodes[SN_GROUP]
        matname = mtl_attrs.get("Name")
        write_attrib_to_mat(mat, mtl_attrs, "PublicParams")

        viewport_trans = True

        # Viewport material values
        set_viewport(mat, mtl_attrs, True)

        shadergroup.node_tree = bpy.data.node_groups['_Hologramcig']
        mat.node_tree.links.new(shadergroup.outputs['BSDF'], shaderout.inputs['Surface'])
        
        return mat


    def create_simple_material(self, mtl_attrs):
        mat, created = self.get_or_create_shader_material(mtl_attrs["Name"])
        if not created:
            return
        nodes = mat.node_tree.nodes
        shaderout = mat.node_tree.nodes[SN_OUT]
        shadergroup = mat.node_tree.nodes[SN_GROUP]

        # Viewport
        mat.blend_method = 'OPAQUE'
        mat.shadow_method = 'OPAQUE'
        # Shader
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        nodes.clear()
        shaderout = nodes.new(type="ShaderNodeOutputMaterial")
        shadergroup = nodes.new('ShaderNodeBsdfPrincipled')
        mat.node_tree.links.new(shadergroup.outputs['BSDF'], shaderout.inputs['Surface'])

        if mtl_attrs.get("Diffuse"):
            shadergroup.inputs[0].default_value = (make_tuple(mtl_attrs["Diffuse"] + ",1"))
        # shadergroup.diffuse_color = mat.diffuse_color
        shaderout.location.x += 300

        if mtl_attrs.get("Textures"):
            self.load_textures(mtl_attrs["Textures"], mat, shadergroup)

        for node in nodes:
            if node.type == 'TEX_IMAGE':            
                if node.name in ['TexSlot1']:
                    try: #diffuse/opacity textures
                        mat.node_tree.links.new(node.outputs[0], shadergroup.inputs[0])
                        mat.node_tree.links.new(node.outputs[1], shadergroup.inputs[19])
                    except:
                        logger.error(f'Could not link {node.name} to {shadergroup.name}')
                        pass

        return mat

    def create_proxy_material(self, mtl):
        mat = (bpy.data.materials.get(mtl["Name"]) or bpy.data.materials.new(mtl["Name"]))
        if mat.get('filename'):
            return  # mtl has already been loaded

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

    def load_textures(self, textures, mat, shadergroup=None):

        y = 0

        def isTexSlot3():
            for tex in textures:
                if tex.get("Map") in ['TexSlot3']:
                    return True
            return False

        nodes = mat.node_tree.nodes if hasattr(mat, 'node_tree') else getattr(mat, 'nodes')
        if nodes is None:
            return logger.error(f'Could not determine nodes for mat {mat.name}')

        for tex in textures:
            filename = tex.get('File')
            logger.debugscbp(f'texture %s', tex.attrib)
            if filename == 'nearest_cubemap':
                continue

            if (tex.get("Map") in ['TexSlot2', 'Bumpmap']) and (
                    isTexSlot3() is False):  # create a seperate entry for glossmap if none specified
                newtex = deepcopy(tex)
                newtex.set('Map', 'TexSlot2A')
                textures.append(newtex)

            if tex.get("Map") in ['TexSlot3', 'TexSlot2A']:
                filename = filename.replace('ddna', 'ddna.glossMap')
            try:
                img = image_for_texture(filename, self.data_dir)
            except FileNotFoundError:
                logger.warning(f"missing texture for mat %s: %s", mat.name, filename)
                continue

            if 'diff' in img.name:
                img.colorspace_settings.name = 'sRGB'
            else:
                img.colorspace_settings.name = 'Non-Color'

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
                mapnode = nodes.new(type='ShaderNodeMapping')
                uvnode = nodes.new(type='ShaderNodeUVMap')
                if texmod.get('TileU') and texmod.get('TileV'):
                    mapnode.inputs['Scale'].default_value = (float(texmod.get('TileU')), float(texmod.get('TileV')), 1)
                    if mapnode.inputs['Scale'].default_value == [0, 0, 1]:
                        mapnode.inputs['Scale'].default_value = [1, 1, 1]
                    try:
                        mat.node_tree.links.new(mapnode.outputs['Vector'], texnode.inputs['Vector'])
                    except:
                        pass
                    try:
                        mat.node_tree.links.new(uvnode.outputs['UV'], mapnode.inputs['Vector'])
                    except:
                        pass
                mapnode.location = texnode.location
                mapnode.location.x -= 300
                uvnode.location = mapnode.location
                uvnode.location.x -= 300
                # mat.node_tree.links.new(mapnode.outputs['Vector'], texnode.inputs['Vector'])

            if not hasattr(mat, 'node_tree'):
                # logger.error("shader node tree doesn't exist")
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
                        # logger.error("failed to link Diffuse Map")
                        pass
            elif tex.get("Map") in ['TexSlot2', 'Bumpmap']:
                try:
                    mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['ddna Color'])
                    mat.node_tree.links.new(texnode.outputs['Alpha'], shadergroup.inputs['ddna Alpha'])
                except:
                    try:
                        mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['Primary ddna Color'])
                    except:
                        pass
            elif tex.get("Map") in ['TexSlot3', 'TexSlot2A']:
                try:
                    mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['ddna Alpha'])
                    # mat.node_tree.links.new(texnode.outputs['Alpha'], shadergroup.inputs['ddna Alpha'])
                except:
                    try:
                        mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['Primary ddna Color'])
                        # mat.node_tree.links.new(texnode.outputs['Alpha'], shadergroup.inputs['Primary ddna Alpha'])
                    except:
                        # logger.error("failed to link DDNA Map")
                        pass
            elif tex.get("Map") in ['TexSlot4', 'Specular']:
                try:
                    mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['spec Color'])
                except:
                    pass
            elif tex.get("Map") in ['TexSlot6']:
                try:
                    mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['detail Color'])
                    mat.node_tree.links.new(texnode.outputs['Alpha'], shadergroup.inputs['detail Alpha'])
                except:
                    # logger.error("failed to link detail Map")
                    pass
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
                    # logger.error("failed to link Decal Map")
                    pass
            elif tex.get("Map") in ['TexSlot11', 'WDA']:
                try:
                    mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['wda Color'])
                    mat.node_tree.links.new(texnode.outputs['Alpha'], shadergroup.inputs['wda Alpha'])
                except:
                    # logger.error("failed to link WDA Map")
                    pass
            elif tex.get("Map") in ['TexSlot12', 'Blendmap']:
                try:
                    mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['blend Color'])
                except:
                    # logger.error("failed to link Blend Map")
                    pass
            elif tex.get("Map") in ['TexSlot13', 'Blendmap']:
                try:
                    # mat.node_tree.links.new(texnode.outputs['Color'], shadergroup.inputs['detail Color'])
                    # mat.node_tree.links.new(texnode.outputs['Alpha'], shadergroup.inputs['detail Alpha'])
                    pass
                except:
                    # logger.error("failed to link detail Map")
                    pass

        return mat


def load_materials(materials, data_dir, tint_palette_node_group=None):
    loader = MTLLoader(data_dir=data_dir, tint_palette_node_group=tint_palette_node_group)
    for mat in tqdm.tqdm(materials, desc='Loading materials', unit='mats'):
        if not mat:
            continue
        if not Path(mat).is_file():
            mat = data_dir / mat
            if not mat.is_file():
                logger.error(f'Could not find mtl file %s', mat)
                continue
        try:
            loader.create_materials_from_mtl(mat)
        except Exception as e:
            logger.exception(f'failed to import material %s', mat, exc_info=e)


def load_tint_palette(palette_file, tint_palette_node_group, data_dir=''):
    p = Path(data_dir) / palette_file if data_dir else Path(palette_file)
    if not p.is_file():
        return

    logger.debugscbp(f'loading tint palette {palette_file} for {tint_palette_node_group.name}')

    t = tint_palette_node_group
    name_map = {
        'entryA': 'Primary',
        'entryB': 'Secondary',
        'entryC': 'Tertiary',
    }

    x = xml.etree.ElementTree.parse(p.open())
    for entry in ['entryA', 'entryB', 'entryC']:
        t.nodes['Outputs'].inputs[name_map[entry]].default_value = a_to_c(x.find(f'.//{entry}/tintColor').attrib)
        attrs = x.find(f'.//{entry}/specColor').attrib
        t.nodes['Outputs'].inputs[f'{name_map[entry]} SpecColor'].default_value = a_to_c(attrs)
        t.nodes['Outputs'].inputs[f'{name_map[entry]} Glossiness'].default_value = (
            float(x.find(f'.//{entry}').attrib['glossiness'])/255
        )

    t.nodes['Outputs'].inputs['Glass Color'].default_value = a_to_c(x.find(f'.//glassColor').attrib)

    if 'DecalConverter' not in t.nodes:
        return  # Decals handling not loaded

    decal_texture = Path(x.find('.//root').attrib['decalTexture'])

    if decal_texture.name:
        try:
            t.nodes['Decal'].image = image_for_texture(decal_texture, data_dir=data_dir)
            t.nodes['Decal'].image.colorspace_settings.name = "Non-Color"
        except FileNotFoundError:
            t.nodes['Decal'].image = None

    for decalColor in ['decalColorR', 'decalColorG', 'decalColorB']:
        t.nodes['DecalConverter'].inputs[decalColor].default_value = a_to_c(x.find(f'.//{decalColor}').attrib)


class LoadTintPalette(Operator):
    """ Load tint palette """
    bl_idname = "scdt.sc_load_tint_palette"
    bl_label = "Load Tint Palette for Entity"

    palette_file: bpy.props.StringProperty(name="palette_file")
    tint_node_group_name: bpy.props.StringProperty(name="tint_node_group_name")
    data_dir: bpy.props.StringProperty(name="data_dir")

    def execute(self, context):
        if self.tint_node_group_name not in bpy.data.node_groups:
            return {'CANCELLED'}

        try:
            load_tint_palette(self.palette_file, bpy.data.node_groups[self.tint_node_group_name], self.data_dir)
        except Exception as e:
            logger.error(f'Failed to sent tint {self.palette_file} on {self.tint_node_group_name}', exc_info=e)
            return {'CANCELLED'}
        return {'FINISHED'}


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

        # load_materials([dirpath / _.name for _ in self.files],
        #               data_dir=self.import_data_dir, use_setting=self.use_setting)
        load_materials([dirpath / _.name for _ in self.files],
                       data_dir=self.import_data_dir)

        return {'FINISHED'}


# Only needed if you want to add into a dynamic menu
def menu_func_import(self, context):
    self.layout.operator(ImportSCMTL.bl_idname, text="Import SC Materials")


def register():
    bpy.utils.register_class(LoadSCShaderNodes)
    bpy.utils.register_class(ImportSCMTL)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.utils.register_class(LoadTintPalette)


def unregister():
    bpy.utils.unregister_class(LoadSCShaderNodes)
    bpy.utils.unregister_class(ImportSCMTL)
    bpy.utils.unregister_class(LoadTintPalette)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
