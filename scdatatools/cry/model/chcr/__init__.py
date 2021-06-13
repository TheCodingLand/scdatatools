import ctypes
from pathlib import Path
from enum import IntEnum
from scdatatools.cry.utils import FileHeaderStructure

from scdatatools.cry.model import chunks

FILE_SIGNATURE = b'CrCh'


class ChCrVersion(IntEnum):
    CRYTEK_3_6 = 0x746
    # CRYTEK_3_5 = 0x745
    # CRYTEK_3_4 = 0x744


class ChCrHeader(ctypes.LittleEndianStructure, FileHeaderStructure):
    _fields_ = [
        ("signature", ctypes.c_uint32),
        ("version", ctypes.c_uint32),
        ("num_chunks", ctypes.c_uint32),
        ("chunk_table_offset", ctypes.c_uint32),
    ]
    _map = {
        "version": ChCrVersion
    }


class ChCr:
    def __init__(self, chcr_file):
        self.filename = Path(chcr_file).absolute()
        with self.filename.open('rb') as f:
            self.raw_data = bytearray(f.read())

        self.header = ChCrHeader.from_buffer(self.raw_data, 0)
        if self.header.signature != FILE_SIGNATURE:
            raise ValueError(f'Invalid file signature for ChCr: {self.header.signature}')

        offset = self.header.chunk_table_offset
        self._chunk_headers = [
            chunks.ChunkHeader.from_buffer(self.raw_data, offset + (i * ctypes.sizeof(chunks.ChunkHeader)))
            for i in range(self.header.num_chunks)
        ]

        self.chunks = {
            h.id: chunks.from_header(h, self.raw_data) for h in self._chunk_headers
        }

    @property
    def version(self):
        return self.header.version

    @property
    def num_chunks(self):
        return self.header.num_chunks
