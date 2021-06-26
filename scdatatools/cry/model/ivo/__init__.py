import sys
import ctypes
from pathlib import Path
from enum import IntEnum
from scdatatools.utils import FileHeaderStructure

from scdatatools.cry.model import chunks

IVO_FILE_SIGNATURE = b'#ivo'


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


class Ivo:
    EXTENSIONS = ('.chr', '.skin', '.skinm')

    def __init__(self, iso_file_or_data):
        if isinstance(iso_file_or_data, str):
            self.filename = Path(iso_file_or_data).absolute()
            if self.filename.suffix not in self.EXTENSIONS:
                raise ValueError(f'Invalid extension for IvoCharacter: {self.filename.suffix}')

            with self.filename.open('rb') as f:
                self.raw_data = bytearray(f.read())
        else:
            self.filename = ''
            self.raw_data = bytearray(iso_file_or_data)

        self.header = IvoHeader.from_buffer(self.raw_data, 0)
        if self.header.signature != IVO_FILE_SIGNATURE:
            raise ValueError(f'Invalid file signature for #ivo: {self.header.signature}')

        offset = self.header.chunk_hdr_table_offset
        self._chunk_headers = [
            chunks.IvoChunkHeader.from_buffer(self.raw_data, offset + (i * ctypes.sizeof(chunks.IvoChunkHeader)))
            for i in range(self.header.num_chunks)
        ]

        self.chunks = {}
        for h in self._chunk_headers:
            try:
                self.chunks[h.type.name] = chunks.ivo_chunk_from_header(h, self.raw_data)
            except Exception as e:
                sys.stderr.write(f'\nError processing chunk {repr(h)}: {repr(e)}\n')
                self.chunks[h.type.name] = chunks.Chunk900(h, self.raw_data)

    @property
    def version(self):
        return self.header.version

    @property
    def num_chunks(self):
        return self.header.num_chunks
