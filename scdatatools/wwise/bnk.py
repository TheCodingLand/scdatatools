import ctypes
from pathlib import Path

from .wem import Wem
from .defs import bnk as bnk_defs


class SoundBank:
    def __init__(self, file_or_buffer):
        if isinstance(file_or_buffer, (str, Path)):
            self.raw_data = bytearray(Path(file_or_buffer).open('rb').read())
        else:
            self.raw_data = bytearray(file_or_buffer)

        offset = 0
        while offset < len(self.raw_data):
            signature = self.raw_data[offset:offset + 4]
            hdr = bnk_defs.HEADER_FOR_SIGNATURE[bytes(signature)].from_buffer(self.raw_data, offset)
            hdr.offset = offset
            setattr(self, signature.decode('utf-8', errors='ignore').strip().lower(), hdr)
            offset += hdr.length + 8

        self.wems = {_.id: _ for _ in self.didx.wem_hdrs} if hasattr(self, 'didx') else {}

    def extract_wem(self, id, filename):
        wem_hdr = self.wems[id]
        wem_offset = self.data.offset + 8 + wem_hdr.data_offset
        wem = Wem(data=self.raw_data[wem_offset:wem_offset + wem_hdr.length])
        wem.write_file(filename)
