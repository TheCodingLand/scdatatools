import ctypes
import logging
import struct

import numpy as np

from scdatatools.engine.model_utils import Vector3D
from .. import defs
from ..base import Chunk

logger = logging.getLogger(__name__)
CHUNK_STR_LEN = 256


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


class UnknownIncludedObjectType(IncludedObjectType):
    _pack_ = 1
    _fields_ = [
        ("object_type", ctypes.c_uint32),
        ("data", ctypes.c_byte * 180)
    ]

    @property
    def filename(self):
        return ""

    def __str__(self):
        return ""


class IncludedObjectType1(IncludedObjectType):
    _pack_ = 1
    _fields_ = [
        ("object_type", ctypes.c_uint32),
        ("raw_vector1", ctypes.c_double * 3),
        ("raw_vector2", ctypes.c_double * 3),
        ("unknown1", ctypes.c_uint64),
        ("id", ctypes.c_uint16),
        ("unknown2", ctypes.c_uint16),
        ("raw_rotMatrix", ctypes.c_double * 12),
        ("unknown3", ctypes.c_uint16 * 10),
        ("flags", ctypes.c_uint32),
        ("unknown4", ctypes.c_uint16 * 4)
    ]

    @classmethod
    def from_buffer(cls, source, offset, io_chunk):
        obj = type(cls).from_buffer(cls, source, offset)

        if obj.unknown4[1] == 0 and cls != IncludedObjectType1Extended:
            return IncludedObjectType1Extended.from_buffer(source, offset, io_chunk)

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
        return Vector3D(
            *[
                np.sqrt(np.dot(self.rotMatrix[:, 0], self.rotMatrix[:, 0])),
                np.sqrt(np.dot(self.rotMatrix[:, 1], self.rotMatrix[:, 1])),
                np.sqrt(np.dot(self.rotMatrix[:, 2], self.rotMatrix[:, 2])),
            ]
        )

    @property
    def rotation(self):
        return self.rotMatrix[:3, :3]

    def __str__(self):
        s = f"""[{self.id}] {self.filename}:\n\t\t"""
        s += "\n\t\t".join(f"{a}: {getattr(self, a)}" for a in ["pos", "scale", "rotation"])
        return s

    def __repr__(self):
        return f"<{self.__class__.__name__} id:{self.id}>"


class IncludedObjectType1Extended(IncludedObjectType1):
    _fields_ = [
        ("unknown5", ctypes.c_uint32 * 2)
    ]


class IncludedObjectType7(IncludedObjectType):
    # TODO: figure this chunk out
    _pack_ = 1
    _fields_ = [
        ("object_type", ctypes.c_uint32),
        ("unknown", ctypes.c_byte * 148),
    ]


class IncludedObjectType10(IncludedObjectType):
    # TODO: figure this chunk out
    _pack_ = 1
    _fields_ = [
        ("object_type", ctypes.c_uint32),
        ("unknown", ctypes.c_byte * 132),
    ]


INCLUDED_OBJECT_TYPES = {
    0x00000001: IncludedObjectType1,
    0x00000007: IncludedObjectType7,
    0x00000010: IncludedObjectType10,
    # TODO: other ICOs
    #   0x00000007?
    #   0x00000010?
    #   0x0000ffff
}


@defs.chunk_handler(defs.ChunkType.IncludedObjects, versions=[0x0001])
class IncludedObjects(Chunk):
    def __init__(self, header, data, model):
        super().__init__(header, data, model)

        self.cgfs = []
        self.materials = []
        self.tint_palettes = []
        self.objects = []

        self.chunk_data.read(4)  # first 4 bytes are 0
        # read cgfs
        num_cgfs = struct.unpack("<I", self.chunk_data.read(4))[0]
        for i in range(num_cgfs):
            self.cgfs.append(self.chunk_data.read(CHUNK_STR_LEN).strip(b"\x00").decode("utf-8"))

        # read mtls/palettes
        num_mtls, num_palettes = struct.unpack("<HH", self.chunk_data.read(4))
        for i in range(num_mtls):
            self.materials.append(
                self.chunk_data.read(CHUNK_STR_LEN).strip(b"\x00").decode("utf-8")
            )

        # read tint palettes
        for i in range(num_palettes):
            self.tint_palettes.append(
                self.chunk_data.read(CHUNK_STR_LEN).strip(b"\x00").decode("utf-8")
            )

        self.filenames = self.cgfs + self.materials

        self.chunk_data.read(28)  # skip 7 unknown uint32
        len_objects = struct.unpack("<I", self.chunk_data.read(4))[0]

        _last_known = 0
        while len_objects > 0:
            obj_type = struct.unpack("<I", self.chunk_data.peek(4))[0]
            obj_class = INCLUDED_OBJECT_TYPES.get(obj_type)

            if obj_class is None:
                obj_class = UnknownIncludedObjectType
            try:
                self.objects.append(
                    obj_class.from_buffer(self.chunk_data.data, self.chunk_data.tell(), self)
                )
            except ValueError as e:
                logger.error(f'Unable to process IncludedObject {self.chunk_header} in {self.chunk_file.filename}, bailing out: {e} ')
                return
            obj_size = ctypes.sizeof(self.objects[-1])
            self.chunk_data.seek(obj_size)
            len_objects -= obj_size
            #     # TODO: This is brute force-y and hack-y and i dont like it. but there seems to be a ton of variation in
            #     #  the data found between chunks that i havent quite been able to pin down. it _seems_ to be safe to
            #     #  work this way though
            #     if _last_known == 0:
            #         _last_known = self.chunk_data.tell()
            #     # skip a uint32
            #     self.chunk_data.seek(4)
            #     len_objects -= 4
            # else:
            #     if _last_known > 0:
            #         logger.warning(
            #             f"SOC IncludedObject: Skipped block of {self.chunk_data.tell() - _last_known} bytes "
            #             f"starting at 0x{_last_known:x} - "
            #             f"{hexdump.dump(self.chunk_data.data[_last_known:_last_known+4])}"
            #         )
            #         _last_known = 0
            #     self.objects.append(
            #         obj_class.from_buffer(self.chunk_data.data, self.chunk_data.tell(), self)
            #     )
            #     obj_size = ctypes.sizeof(self.objects[-1])
            #     self.chunk_data.seek(obj_size)
            #     len_objects -= obj_size
        if _last_known > 0:
            logger.debug(
                f"SOC IncludedObject: Skipped block of {self.chunk_data.tell() - _last_known} "
                f"bytes starting at 0x{_last_known:x}"
            )
        if len_objects != 0:
            raise Exception()

    def __str__(self):
        cgfs = "\n    ".join(self.cgfs)
        materials = "\n    ".join(self.materials)
        tints = "\n    ".join(self.tint_palettes)
        objects = ""
        for object in self.objects:
            try:
                objects += f"\n    {str(object)}"
            except Exception as e:
                objects += f"\n    {repr(object)} ({repr(e)})"
        return f"""Geometry:
    {cgfs}
    
Materials:
    {materials}
    
Tint Palettes:
    {tints}
    
Objects:
    {objects}
"""

    def __repr__(self):
        return f"<IncludedObjects cgfs:{len(self.cgfs)} mtls:{len(self.materials)} tints:{len(self.tint_palettes)}>"
