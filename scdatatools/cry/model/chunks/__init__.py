import ctypes
import json
from io import BytesIO

from scdatatools.utils import StructureWithEnums
from scdatatools.cry.cryxml import etree_from_cryxml_file, dict_from_cryxml_file

from . import defs


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

    def __repr__(self):
        return f'<Chunk type:{repr(self.header.type)} id:{self.id} size:{self.header.size} offset:{self.header.offset}>'

    @property
    def id(self):
        return self.header.id

    @classmethod
    def from_buffer(cls, header, data):
        return cls(header, data[header.offset:header.offset+header.size])


class Chunk900:
    size = 0

    def __init__(self, header, data):
        self.header = header
        self.data = data

    def __repr__(self):
        return f'<Chunk900 type:{repr(self.header.type)} size:{self.size} offset:{self.header.offset}>'

    @classmethod
    def from_buffer(cls, header, data):
        return cls(header, data[header.offset:header.offset+cls.size])


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
    defs.ChunkType.UnknownSC3: Chunk,
    defs.ChunkType.UnknownSC4: Chunk,
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
