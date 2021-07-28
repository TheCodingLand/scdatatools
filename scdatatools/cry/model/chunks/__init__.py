import json
import ctypes
import struct
import logging
from io import BytesIO

import hexdump
import numpy as np
from pyquaternion import Quaternion

from scdatatools.utils import StructureWithEnums
from scdatatools.cry.cryxml import etree_from_cryxml_file, dict_from_cryxml_file
from scdatatools.cry.model.utils import Vector3D

from . import defs

logger = logging.getLogger(__name__)
CHUNK_STR_LEN = 256


class IvoChunkHeader(ctypes.LittleEndianStructure, StructureWithEnums):
    _fields_ = [
        ('type', ctypes.c_uint32),
        ('version', ctypes.c_uint32),
        ('offset', ctypes.c_uint64),
    ]
    _map = {
        'type': (defs.CharacterChunkHeaderTypes, defs.DBAChunkHeaderTypes, defs.AIMChunkHeaderTypes)
    }


class ChunkHeader(ctypes.LittleEndianStructure, StructureWithEnums):
    _fields_ = [
        ('type', ctypes.c_uint16),
        ('version', ctypes.c_uint16),
        ('id', ctypes.c_uint32),
        ('size', ctypes.c_uint32),
        ('offset', ctypes.c_uint32),
    ]
    _map = {
        'type': defs.ChunkType
    }


class Chunk:
    def __init__(self, header, data):
        self.header = header
        self.data = data
        self._offset = 0

    def read(self, length=None):
        try:
            if length is None:
                length = len(self.data) - self._offset
            return self.data[self._offset:self._offset + length]
        finally:
            self._offset = min(self._offset + length, len(self.data))

    def peek(self, length=None):
        if length is None:
            length = len(self.data) - self._offset
        return self.data[self._offset:self._offset + length]

    def tell(self):
        return self._offset

    def seek(self, offset, whence=1):
        if whence == 0:
            new_offset = offset
        elif whence == 1:
            new_offset = self._offset + offset
        elif whence == 2:
            new_offset = len(self.data) + offset
        else:
            raise ValueError(f'Invalid whence value "{whence}"')

        if new_offset > len(self.data):
            raise IndexError(f'index out of range')
        self._offset = new_offset

    def __repr__(self):
        return f'<Chunk type:{repr(self.header.type)} id:{self.id} size:{self.header.size} offset:{self.header.offset}>'

    @property
    def id(self):
        return self.header.id

    @classmethod
    def from_buffer(cls, header, data):
        return cls(header, data[header.offset:header.offset + header.size])


class Chunk900(Chunk):
    size = 0

    def __repr__(self):
        return f'<Chunk900 type:{repr(self.header.type)} size:{self.size} offset:{self.header.offset}>'

    @property
    def id(self):
        return ''

    @classmethod
    def from_buffer(cls, header, data):
        return cls(header, data[header.offset:header.offset + cls.size])


class MaterialName900(Chunk900):
    size = 128

    def __init__(self, header, data):
        super().__init__(header, data)
        self.name = data.decode('utf-8').strip('\x00')

    def __repr__(self):
        return f'<MaterialName900 name:{self.name} size:{self.size} offset:{self.header.offset}>'


class MtlName(Chunk):
    def __init__(self, header, data):
        super().__init__(header, data)
        self.name = data[:128].decode('utf-8').strip('\x00')
        self.num_children = ctypes.c_uint32.from_buffer(self.data, 128).value
        self.physics_types = [
            defs.MtlNamePhysicsType(ctypes.c_uint32.from_buffer(self.data, (i * 4) + 132).value)
            for i in range(self.num_children)
        ]
        self.mat_type = defs.MtlNameType.Single if self.num_children == 0 else defs.MtlNameType.Library

    def __str__(self):
        phys_types = '\n    '.join(str(_) for _ in self.physics_types)
        return \
            f"""Material: {self.name}
Type: {self.mat_type}
Children: {self.num_children}
Physics Types:
    {phys_types}
"""

    def __repr__(self):
        return f'<MtlName name:{self.name} type:{self.mat_type.name} id:{self.id} children:{self.num_children}>'


class SourceInfoChunk(Chunk):
    def __init__(self, header, data):
        super().__init__(header, data)
        self.raw_data = data
        self.data = '\n'.join(self.raw_data.decode('utf-8').split('\x00'))


class BoneNameList(Chunk):
    def __init__(self, header, data):
        super().__init__(header, data)
        self.names = self.data[4:-2].decode('utf-8').split('\x00')


class CryXMLBChunk(Chunk):
    def dict(self):
        return dict_from_cryxml_file(BytesIO(self.data))

    def etree(self):
        return etree_from_cryxml_file(BytesIO(self.data))


class JSONChunk(Chunk):
    def dict(self):
        return json.loads(self.data.decode('utf-8'))


class AreaShapeObject(Chunk):
    def __init__(self, header, data):
        super().__init__(header, data)

        self.vis_areas = []
        self.portals = []

        self.read(4)  # unknown1
        area_shapes_len = struct.unpack('<I', self.read(4))[0]
        num_vis_areas = struct.unpack('<I', self.read(4))[0]
        num_portals = struct.unpack('<I', self.read(4))[0]
        self.read(4)  # unknown2

        # TODO: flesh out the rest of the areashape chunk
        #    dymek had parsed out some of the, what looks to be, old format, could be useful:
        #    https://github.com/dymek91/Exporting-Toolkit/blob/master/shipsExporter/CryEngine/ChCr/SCOC/AreaShapes.cs


class IncludedObjectType(ctypes.LittleEndianStructure):
    _pack_ = 1

    @property
    def filename(self):
        return self.io_chunk.cgfs[self.id]

    @classmethod
    def from_buffer(cls, source, offset, io_chunk):
        obj = type(cls).from_buffer(cls, source, offset)
        obj.source_offset = offset
        obj.io_chunk = io_chunk
        return obj


class IncludedObjectType0(IncludedObjectType):
    _pack_ = 1
    _fields_ = [('object_type', ctypes.c_uint32), ('unknown', ctypes.c_uint32)]

    @property
    def filename(self):
        return ''

    def __str__(self):
        return ''


class IncludedObjectType1(IncludedObjectType):
    _pack_ = 1
    _fields_ = [
        ('object_type', ctypes.c_uint32),
        ('raw_vector1', ctypes.c_double * 3),
        ('raw_vector2', ctypes.c_double * 3),
        ('unknown1', ctypes.c_uint64),
        ('id', ctypes.c_uint16),
        ('temp1', ctypes.c_uint16),
        ('raw_rotMatrix', ctypes.c_double * 12),
        ('unknown', ctypes.c_uint32 * 4),
    ]

    @classmethod
    def from_buffer(cls, source, offset, io_chunk):
        obj = type(cls).from_buffer(cls, source, offset)
        obj.source_offset = offset
        obj.io_chunk = io_chunk
        obj.vector1 = np.array(obj.raw_vector1)
        obj.vector2 = np.array(obj.raw_vector2)
        obj.rotMatrix = np.array(obj.raw_rotMatrix).reshape((3, 4))
        return obj

    @property
    def pos(self) -> dict:
        return Vector3D(*self.rotMatrix[:, 3])

    @property
    def scale(self) -> dict:
        return Vector3D(*[
            np.sqrt(np.dot(self.rotMatrix[:, 0], self.rotMatrix[:, 0])),
            np.sqrt(np.dot(self.rotMatrix[:, 1], self.rotMatrix[:, 1])),
            np.sqrt(np.dot(self.rotMatrix[:, 2], self.rotMatrix[:, 2]))
        ])

    @property
    def rotation(self):
        return self.rotMatrix[:3, :3]

    def __str__(self):
        s = f"""[{self.id}] {self.filename}:\n\t\t"""
        s += '\n\t\t'.join(f'{a}: {getattr(self, a)}' for a in ['pos', 'scale', 'rotation'])
        return s

    def __repr__(self):
        return f'<{self.__class__.__name__} id:{self.id}>'


INCLUDED_OBJECT_TYPES = {
    0x0000: IncludedObjectType0,
    0x0001: IncludedObjectType1,
    # TODO: other ICOs
    #   0x07?
    #   0x10?
}


class IncludedObjects(Chunk):
    def __init__(self, header, data):
        super().__init__(header, data)

        self.cgfs = []
        self.materials = []
        self.tint_palettes = []
        self.objects = []

        self.read(4)  # first 4 bytes are 0
        # read cgfs
        num_cgfs = struct.unpack('<I', self.read(4))[0]
        for i in range(num_cgfs):
            self.cgfs.append(self.read(CHUNK_STR_LEN).strip(b'\x00').decode('utf-8'))

        # read mtls/palettes
        num_mtls, num_palettes = struct.unpack('<HH', self.read(4))
        for i in range(num_mtls):
            self.materials.append(self.read(CHUNK_STR_LEN).strip(b'\x00').decode('utf-8'))

        # read tint palettes
        for i in range(num_palettes):
            self.tint_palettes.append(self.read(CHUNK_STR_LEN).strip(b'\x00').decode('utf-8'))

        self.filenames = self.cgfs + self.materials

        self.read(28)  # skip 7 unknown uint32
        len_objects = struct.unpack('<I', self.read(4))[0]

        _last_known = 0
        while len_objects > 0:
            obj_type = struct.unpack('<I', self.peek(4))[0]
            obj_class = INCLUDED_OBJECT_TYPES.get(obj_type)

            if obj_class is None:
                # TODO: This is brute force-y and hack-y and i dont like it. but there seems to be a ton of variation in
                #  the data found between chunks that i havent quite been able to pin down. it _seems_ to be safe to
                #  work this way though
                if _last_known == 0:
                    _last_known = self.tell()
                # skip a uint32
                self.seek(4)
                len_objects -= 4
            else:
                if _last_known > 0:
                    logger.warning(f'SOC IncludedObject: Skipped block of {self.tell() - _last_known} bytes starting '
                                   f'at 0x{_last_known:x} - {hexdump.dump(self.data[_last_known:_last_known+4])}')
                    _last_known = 0
                self.objects.append(obj_class.from_buffer(self.data, self.tell(), self))
                obj_size = ctypes.sizeof(self.objects[-1])
                self.seek(obj_size)
                len_objects -= obj_size
        if _last_known > 0:
            logger.debug(f'SOC IncludedObject: Skipped block of {self.tell() - _last_known} bytes starting at '
                         f'0x{_last_known:x}')
        assert (len_objects == 0)

    def __str__(self):
        cgfs = '\n    '.join(self.cgfs)
        materials = '\n    '.join(self.materials)
        tints = '\n    '.join(self.tint_palettes)
        objects = ''
        for object in self.objects:
            try:
                objects += f'\n    {str(object)}'
            except Exception as e:
                objects += f'\n    {repr(object)} ({repr(e)})'
        return \
            f"""Geometry:
    {cgfs}
    
Materials:
    {materials}
    
Tint Palettes:
    {tints}
    
Objects:
    {objects}
"""

    def __repr__(self):
        return f'<IncludedObjects cgfs:{len(self.cgfs)} mtls:{len(self.materials)} tints:{len(self.tint_palettes)}>'


IVO_CHUNK_FOR_TYPE = {
    defs.CharacterChunkHeaderTypes.Physics: Chunk900,
    defs.CharacterChunkHeaderTypes.BShapesGPU: Chunk900,
    defs.CharacterChunkHeaderTypes.MaterialName: MaterialName900,
    defs.CharacterChunkHeaderTypes.BShapes: Chunk900,
    defs.CharacterChunkHeaderTypes.SkinInfo: Chunk900,
    defs.CharacterChunkHeaderTypes.SkinMesh: Chunk900,
    defs.CharacterChunkHeaderTypes.Skeleton: Chunk900,
    defs.DBAChunkHeaderTypes.DBA: Chunk900,
    defs.DBAChunkHeaderTypes.DBAData: Chunk900,
    defs.DBAChunkHeaderTypes.Skeleton: Chunk900,
    defs.DBAChunkHeaderTypes.UNKNOWN1: Chunk900,
    defs.AIMChunkHeaderTypes.Skeleton: Chunk900,
    defs.AIMChunkHeaderTypes.BShapes: Chunk900
}

CHUNK_FOR_TYPE = {
    defs.ChunkType.Any: Chunk,
    defs.ChunkType.Mesh: Chunk,
    defs.ChunkType.Helper: Chunk,
    defs.ChunkType.VertAnim: Chunk,
    defs.ChunkType.BoneAnim: Chunk,
    defs.ChunkType.GeomNameList: Chunk,
    defs.ChunkType.BoneNameList: BoneNameList,
    defs.ChunkType.MtlList: Chunk,
    defs.ChunkType.MRM: Chunk,
    defs.ChunkType.SceneProps: Chunk,
    defs.ChunkType.Light: Chunk,
    defs.ChunkType.PatchMesh: Chunk,
    defs.ChunkType.Node: Chunk,
    defs.ChunkType.Mtl: Chunk,
    defs.ChunkType.Controller: Chunk,
    defs.ChunkType.Timing: Chunk,
    defs.ChunkType.BoneMesh: Chunk,
    defs.ChunkType.BoneLightBinding: Chunk,
    defs.ChunkType.MeshMorphTarget: Chunk,
    defs.ChunkType.BoneInitialPos: Chunk,
    defs.ChunkType.SourceInfo: SourceInfoChunk,
    defs.ChunkType.MtlName: MtlName,
    defs.ChunkType.ExportFlags: Chunk,
    defs.ChunkType.DataStream: Chunk,
    defs.ChunkType.MeshSubsets: Chunk,
    defs.ChunkType.MeshPhysicsData: Chunk,

    # Star Citizen versions
    defs.ChunkType.CompiledBonesSC: Chunk,
    defs.ChunkType.CompiledPhysicalBonesSC: Chunk,
    defs.ChunkType.CompiledMorphTargetsSC: Chunk,
    defs.ChunkType.CompiledPhysicalProxiesSC: Chunk,
    defs.ChunkType.CompiledIntFacesSC: Chunk,
    defs.ChunkType.CompiledIntSkinVerticesSC: Chunk,
    defs.ChunkType.CompiledExt2IntMapSC: Chunk,
    # defs.ChunkType.BoneBoxesSC: Chunk,
    defs.ChunkType.CryXMLB: CryXMLBChunk,
    defs.ChunkType.JSON: JSONChunk,

    defs.ChunkType.UnknownSC1: Chunk,
    defs.ChunkType.UnknownSC2: Chunk,
    defs.ChunkType.AreaShape: AreaShapeObject,
    defs.ChunkType.IncludedObjects: IncludedObjects,
    defs.ChunkType.UnknownSC5: Chunk,
    defs.ChunkType.UnknownSC6: Chunk,
    defs.ChunkType.UnknownSC7: Chunk,
    defs.ChunkType.UnknownSC8: Chunk,
    defs.ChunkType.UnknownSC9: Chunk,
    defs.ChunkType.UnknownSC10: Chunk,
    defs.ChunkType.UnknownSC11: Chunk,
}


def from_header(hdr: ChunkHeader, data: (bytearray, bytes)):
    """

    :param hdr: `ChunkHeader` describing the Chunk in `data`
    :param data: Data to read chunk from
    :return: `Chunk`
    """
    return CHUNK_FOR_TYPE[hdr.type].from_buffer(hdr, data)


def ivo_chunk_from_header(hdr: IvoChunkHeader, data: (bytearray, bytes)):
    """

    :param hdr: `ChunkHeader` describing the Chunk in `data`
    :param data: Data to read chunk from
    :return: `Chunk900`
    """
    return IVO_CHUNK_FOR_TYPE[hdr.type].from_buffer(hdr, data)
