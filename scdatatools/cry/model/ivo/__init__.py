import ctypes
from pathlib import Path
from enum import IntEnum
from scdatatools.cry.utils import FileHeaderStructure

from scdatatools.cry.model import chunks

FILE_SIGNATURE = b'#ivo'


class IvoVersion(IntEnum):
    SC_3_11 = 0x900


class IvoHeader(ctypes.LittleEndianStructure, FileHeaderStructure):
    _fields_ = [                               # #ivo files must be 8-byte aligned
        ("signature", ctypes.c_uint32),        # FILE_SIGNATURE
        ("version", ctypes.c_uint32),          # IvoVersion
        ("num_chunks", ctypes.c_uint32),       # must be  0 < num_chunks < 7
        ("chunk_hdr_table_offset", ctypes.c_uint32)
    ]
    _map = {
        "version": IvoVersion
    }


class IvoCharacter:
    EXTENSIONS = ('.chr', '.skin', '.skinm')

    def __init__(self, iso_file):
        self.filename = Path(iso_file).absolute()

        if self.filename.suffix not in self.EXTENSIONS:
            raise ValueError(f'Invalid extension for IvoCharacter: {self.filename.suffix}')

        with self.filename.open('rb') as f:
            self.raw_data = bytearray(f.read())

        self.header = IvoHeader.from_buffer(self.raw_data, 0)
        if self.header.signature != FILE_SIGNATURE:
            raise ValueError(f'Invalid file signature for #ivo: {self.header.signature}')

        offset = self.header.chunk_hdr_table_offset
        self._chunk_headers = [
            chunks.IvoCharacterChunkHeader.from_buffer(self.raw_data,
                                                       offset + (i * ctypes.sizeof(chunks.IvoCharacterChunkHeader)))

            for i in range(self.header.num_chunks)
        ]

        self.chunks = {
            h.type.name: chunks.ivocharacter_chunk_from_header(h, self.raw_data) for h in self._chunk_headers
        }

    @property
    def version(self):
        return self.header.version

    @property
    def num_chunks(self):
        return self.header.num_chunks
