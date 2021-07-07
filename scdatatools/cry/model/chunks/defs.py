from enum import IntEnum


class ChunkType(IntEnum):
    Any = 0x0,
    Mesh = 0x1000,
    Helper = 0x1001,
    VertAnim = 0x1002,
    BoneAnim = 0x1003,
    GeomNameList = 0x1004,
    BoneNameList = 0x1005,
    MtlList = 0x1006,
    MRM = 0x1007,
    SceneProps = 0x1008,
    Light = 0x1009,
    PatchMesh = 0x100A,
    Node = 0x100B,
    Mtl = 0x100C,
    Controller = 0x100D,
    Timing = 0x100E,
    BoneMesh = 0x100F,
    BoneLightBinding = 0x1010,
    MeshMorphTarget = 0x1011,
    BoneInitialPos = 0x1012,
    SourceInfo = 0x1013,  # Describes the source from which the cgf was exported: source max file, machine and user.
    MtlName = 0x1014,  # provides material name as used in the material.xml file
    ExportFlags = 0x1015,  # Describes export information
    DataStream = 0x1016,  # A data stream
    MeshSubsets = 0x1017,  # Describes an array of mesh subsets
    MeshPhysicsData = 0x1018,  # Physicalized mesh data

    # Star Citizen Types
    CompiledBonesSC = 0x2000,
    CompiledPhysicalBonesSC = 0x2001,
    CompiledMorphTargetsSC = 0x2002,
    CompiledPhysicalProxiesSC = 0x2003,
    CompiledIntFacesSC = 0x2004,
    CompiledIntSkinVerticesSC = 0x2005,
    CompiledExt2IntMapSC = 0x2006,

    CryXMLB = 0x0004,
    JSON = 0x0011,

    UnknownSC1 = 0x3004,  # 'data/objects/spaceships/ships/aegs/javelin/exteriors/aegs_javelin.cga'
    UnknownSC2 = 0x0002,  # 'data/objectcontainers/ships/aegs/javelin/base_int_hab_main/base_int_hab_main.soc'

    # https://github.com/dymek91/Exporting-Toolkit/blob/master/shipsExporter/CryEngine/ChCr/SCOC/Chunk_AreaShape.cs
    UnknownSC3 = 0x000e,  # 'data/objectcontainers/ships/aegs/javelin/base_int_hab_main/base_int_hab_main.soc'

    # https://github.com/dymek91/Exporting-Toolkit/blob/master/shipsExporter/CryEngine/ChCr/SCOC/Chunk_Objects.cs
    UnknownSC4 = 0x0010,  # 'data/objectcontainers/ships/aegs/javelin/base_int_hab_main/base_int_hab_main.soc'
    UnknownSC5 = 0x0008,  # 'data/objectcontainers/ships/aegs/javelin/base_int_hab_main/base_int_hab_main.soc'
    UnknownSC6 = 0x300a,  # 'data/objects/spaceships/ships/aegs/javelin/exteriors/aegs_javelin.cga'
    UnknownSC7 = 0x4002,  # 'data/objects/spaceships/ships/aegs/javelin/exteriors/aegs_javelin.cga'
    UnknownSC8 = 0x3005,  # Data\Objects\planets\flora\bush\bayberry_01\bayberry_01.cgfm: 12293
    UnknownSC9 = 0x0013,
    UnknownSC10 = 0x0014,
    UnknownSC11 = 0x000b,


class DBAChunkHeaderTypes(IntEnum):
    # StarCitizen version 0x900
    # From  IVO_Loader? .dba files
    Skeleton        = 0x0000300d
    DBAData         = 0x194fbc50
    DBA             = 0xf7351608  # is checked against -0x8cae9f8 in code
    UNKNOWN1        = 0x322ba3c7  # found in Data\Animations\Characters\Human\female_v2\force_reactions.dba


class AIMChunkHeaderTypes(IntEnum):
    # From  IVO_Loader2? handles AIM files, .caf?
    Skeleton        = 0x1bbc4103
    BShapes         = 0xf5c6eb5b


class CharacterChunkHeaderTypes(IntEnum):
    """ Types for .chr/.skin """
    # From  IVO_Loader3 seems to handle .chr, .skin
    Physics         = 0x90c687dc
    BShapesGPU      = 0x57a3befd
    MaterialName    = 0x8335674e
    BShapes         = 0x875ccb28
    SkinInfo        = 0x9293b9d8
    SkinMesh        = 0xb875b2d9
    Skeleton        = 0xc201973c


class MtlNameType(IntEnum):
    # It looks like there is a 0x04 type now as well, for mech parts.  Not sure what that is.
    # Also a 0x0B type now as well.
    Library = 0x01,
    MwoChild = 0x02,
    Single = 0x10,
    Child = 0x12,
    Unknown1 = 0x0B,        # Collision materials?  In MWO, these are the torsos, arms, legs from body/<mech>.mtl
    Unknown2 = 0x04


class MtlNamePhysicsType(IntEnum):
    NONE = 0xFFFFFFFF,
    DEFAULT = 0x00000000,
    NOCOLLIDE = 0x00000001,
    OBSTRUCT = 0x00000002,
    DEFAULTPROXY = 0x000000FF,  # this needs to be checked.  cgf.xml says 256; not sure if hex or dec
    UNKNOWN = 0x00001100,       # collision mesh?
    UNKNOWN1 = 0x1000     # found in 'data/objects/spaceships/ships/aegs/javelin/interior/set_tec/tec_ceiling_fan.cgf'
    UNKNOWN2 = 0x1001     # found in javelin.cga
    UNKNOWN3 = 0x1002     # found in Data\Objects\planet\flora\tree\dendrosenecio\dendrosenecio_b_lod4.cgf
