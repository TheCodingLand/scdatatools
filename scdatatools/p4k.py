import io
import re
import os
import json
import shutil
import struct
import zipfile
import fnmatch
from pathlib import Path

import zstandard as zstd
from Crypto.Cipher import AES

from scdatatools.cry.cryxml import etree_from_cryxml_file, pprint_xml_tree, dict_from_cryxml_file


ZIP_ZSTD = 100
p4kFileHeader = b"PK\x03\x14"
DEFAULT_P4K_KEY = b"\x5E\x7A\x20\x02\x30\x2E\xEB\x1A\x3B\xB6\x17\xC3\x0F\xDE\x1E\x47"
CRYXMLB_FORMATS = ['xml', 'mtl', 'chrparams', 'entxml', 'rmp', 'animevents']

compressor_names = zipfile.compressor_names
compressor_names[100] = "zstd"


def _P4KDecrypter(key):
    cipher = AES.new(key, AES.MODE_CBC, b"\x00" * 16)

    def decrypter(data):
        return cipher.decrypt(data)

    return decrypter


class ZStdDecompressor:
    def __init__(self):
        dctx = zstd.ZstdDecompressor()
        self._decomp = dctx.decompressobj()
        self.eof = False

    def decompress(self, data):
        result = b""
        try:
            result = self._decomp.decompress(data)
        except zstd.ZstdError:
            self.eof = True
        return result


class P4KExtFile(zipfile.ZipExtFile):
    MIN_READ_SIZE = 65536

    def __init__(self, fileobj, mode, p4kinfo, decrypter=None, close_fileobj=False):
        self._is_encrypted = p4kinfo.is_encrypted
        self._decompressor = ZStdDecompressor()

        self._fileobj = fileobj
        self._decrypter = decrypter
        self._close_fileobj = close_fileobj

        self._compress_type = p4kinfo.compress_type
        self._compress_left = p4kinfo.compress_size
        self._left = p4kinfo.file_size

        self._eof = False
        self._readbuffer = b""
        self._offset = 0

        self.newlines = None

        self.mode = mode
        self.name = p4kinfo.filename

        if hasattr(p4kinfo, 'CRC'):
            self._expected_crc = p4kinfo.CRC
            self._running_crc = zipfile.crc32(b'')
        else:
            self._expected_crc = None
        # TODO: the CRCs don't match, but im getting the same outputs as unp4k - we should figure out what exactly is
        #   going into calculating the CRC for P4K entry
        self._expected_crc = None

        self._seekable = False
        try:
            if fileobj.seekable():
                self._orig_compress_start = fileobj.tell()
                self._orig_compress_size = p4kinfo.compress_size
                self._orig_file_size = p4kinfo.file_size
                self._orig_start_crc = self._running_crc
                self._seekable = True
        except AttributeError:
            pass


class P4KInfo(zipfile.ZipInfo):
    def __init__(self, *args, subinfo=None, archive=None, **kwargs):
        # ensure posix file paths as specified by the Zip format
        super().__init__(*args, **kwargs)
        self.archive = archive
        self.subinfo = subinfo
        self.filelist = []
        if self.subinfo is not None and self.subinfo.is_dir():
            self.filename += '/'  # Make sure still tracked as directory
        self.is_encrypted = False

    def _decodeExtra(self):
        # Try to decode the extra field.
        self.is_encrypted = len(self.extra) >= 168 and self.extra[168] > 0x00

        # The following is the default ZipInfo decode, minus a few steps that would mark an encrypted it as invalid
        # TODO: only do this if self.is_encrypted, otherwise call super?

        offset = 0
        total = len(self.extra)
        while (offset+4) < total:
            tp, ln = struct.unpack("<HH", self.extra[offset:offset+4])
            if tp == 0x0001:
                if ln >= 24:
                    counts = struct.unpack("<QQQ", self.extra[offset+4:offset+28])
                elif ln == 16:
                    counts = struct.unpack("<QQ", self.extra[offset+4:offset+20])
                elif ln == 8:
                    counts = struct.unpack("<Q", self.extra[offset+4:offset+12])
                elif ln == 0:
                    counts = ()
                else:
                    raise zipfile.BadZipFile(
                        "Corrupt extra field %04x (size=%d)" % (tp, ln)
                    )

                idx = 0
                # ZIP64 extension (large files and/or large archives)
                if self.file_size in (0xFFFFFFFFFFFFFFFF, 0xFFFFFFFF):
                    self.file_size = counts[idx]
                    idx += 1

                if self.compress_size == 0xFFFFFFFF:
                    self.compress_size = counts[idx]
                    idx += 1

                if self.header_offset == 0xFFFFFFFF:
                    self.header_offset = counts[idx]
                    idx += 1
            offset += ln + 4


class P4KFile(zipfile.ZipFile):
    def __init__(self, file, mode="r", key=DEFAULT_P4K_KEY, convert_fmt='json'):
        # Using ZIP_STORED to bypass the get_compressor/get_decompressor logic in zipfile. Our P4KExtFile will always
        # use zstd
        self.key = key
        self.NameToInfoLower = {}
        self.subarchives = {}
        self.convert_fmt = convert_fmt

        super().__init__(file, mode, compression=zipfile.ZIP_STORED)

        # TODO: this doubles the time of opening a pack, so add some logic to delay expansion and add functionality to
        #       expand on the fly
        # open up sub-archives and add them to the file list
        for filename in self.subarchives.keys():
            if self.subarchives[filename] is not None:
                continue  # already been opened
            file_ext = f"{filename.split('.', maxsplit=1)[-1]}"
            info = self.NameToInfo[filename]
            self.subarchives[filename] = SUB_ARCHIVES[file_ext](self.open(info))
            info.subarchive = self.subarchives[filename]

            base_p4k_path = Path(filename[:-1 * len(file_ext) - 1])
            archive_name = base_p4k_path.name
            for si in info.subarchive.filelist:
                # Create ZipInfo instance to store file information
                # parent extension gets removed
                # e.g. `100i_interior.socpak` -> `100i_interior`
                sub_path = Path(si.filename)
                if sub_path.parts[0] == archive_name:
                    # Paths in socpaks are referenced slightly strangely.
                    sub_path = Path(*sub_path.parts[1:])
                x = P4KInfo(str(base_p4k_path / sub_path),
                            subinfo=si, archive=self.subarchives[filename])
                x.extra = si.extra
                x.comment = si.comment
                x.header_offset = si.header_offset
                x.volume, x.internal_attr, x.external_attr = si.volume, si.internal_attr, si.external_attr
                x._raw_time = si._raw_time
                x.date_time = si.date_time
                x.create_version = si.create_version
                x.create_system = si.create_system
                x.extract_version = si.extract_version
                x.reserved = si.reserved
                x.flag_bits = si.flag_bits
                x.compress_type = si.compress_type
                x.CRC = si.CRC
                x.compress_size = si.compress_size
                x.file_size = si.file_size

                info.filelist.append(x)
                self.filelist.append(x)
                self.NameToInfo[x.filename] = x
                self.NameToInfoLower[x.filename.lower()] = x

    def _RealGetContents(self):
        """Read in the table of contents for the ZIP file."""
        fp = self.fp
        try:
            endrec = zipfile._EndRecData(fp)
        except OSError:
            raise zipfile.BadZipFile("File is not a valid p4k file")
        if not endrec:
            raise zipfile.BadZipFile("File is not a valid p4k file")
        size_cd = endrec[zipfile._ECD_SIZE]  # bytes in central directory
        offset_cd = endrec[zipfile._ECD_OFFSET]  # offset of central directory
        self._comment = endrec[zipfile._ECD_COMMENT]  # archive comment

        # "concat" is zero, unless zip was concatenated to another file
        concat = endrec[zipfile._ECD_LOCATION] - size_cd - offset_cd
        if endrec[zipfile._ECD_SIGNATURE] == zipfile.stringEndArchive64:
            # If Zip64 extension structures are present, account for them
            concat -= zipfile.sizeEndCentDir64 + zipfile.sizeEndCentDir64Locator

        # self.start_dir:  Position of start of central directory
        self.start_dir = offset_cd + concat
        fp.seek(self.start_dir, 0)
        data = io.BytesIO(fp.read(size_cd))
        while data.tell() < size_cd:
            centdir = data.read(zipfile.sizeCentralDir)
            if len(centdir) != zipfile.sizeCentralDir:
                raise zipfile.BadZipFile("Truncated central directory")
            centdir = struct.unpack(zipfile.structCentralDir, centdir)
            if centdir[zipfile._CD_SIGNATURE] != zipfile.stringCentralDir:
                raise zipfile.BadZipFile("Bad magic number for central directory")

            filename = data.read(centdir[zipfile._CD_FILENAME_LENGTH])
            flags = centdir[5]
            if flags & 0x800:
                # UTF-8 file names extension
                filename = filename.decode("utf-8")
            else:
                # Historical ZIP filename encoding
                filename = filename.decode("cp437")

            # Create ZipInfo instance to store file information
            x = P4KInfo(filename, archive=self)
            x.extra = data.read(centdir[zipfile._CD_EXTRA_FIELD_LENGTH])
            x.comment = data.read(centdir[zipfile._CD_COMMENT_LENGTH])
            x.header_offset = centdir[zipfile._CD_LOCAL_HEADER_OFFSET]
            (
                x.create_version,
                x.create_system,
                x.extract_version,
                x.reserved,
                x.flag_bits,
                x.compress_type,
                t,
                d,
                x.CRC,
                x.compress_size,
                x.file_size,
            ) = centdir[1:12]
            if x.extract_version > zipfile.MAX_EXTRACT_VERSION:
                raise NotImplementedError(
                    "zip file version %.1f" % (x.extract_version / 10)
                )
            # x.volume, x.internal_attr, x.external_attr = centdir[15:18]
            # Convert date/time code to (year, month, day, hour, min, sec)
            x._raw_time = t
            x.date_time = (
                (d >> 9) + 1980,
                (d >> 5) & 0xF,
                d & 0x1F,
                t >> 11,
                (t >> 5) & 0x3F,
                (t & 0x1F) * 2,
            )

            x._decodeExtra()
            x.header_offset = x.header_offset + concat
            self.filelist.append(x)
            self.NameToInfo[x.filename] = x
            self.NameToInfoLower[x.filename.lower()] = x

            # Add sub-archives to be opened later (.pak/.sockpak,etc)
            file_ext = x.filename.split('.', maxsplit=1)[-1]
            if file_ext.lower() in SUB_ARCHIVES:
                if x.filename not in self.subarchives:
                    self.subarchives[x.filename] = None

    def open(self, name, mode="r", pwd=None, *, force_zip64=False):
        """Return file-like object for 'name'.

        name is a string for the file name within the ZIP file, or a ZipInfo
        object.

        mode should be 'r' to read a file already in the ZIP file, or 'w' to
        write to a file newly added to the archive.

        pwd is the password to decrypt files (only used for reading).

        When writing, if the file size is not known in advance but may exceed
        2 GiB, pass force_zip64 to use the ZIP64 format, which can handle large
        files.  If the size is known in advance, it is best to pass a ZipInfo
        instance for name, with zinfo.file_size set.
        """
        if mode not in {"r", "w"}:
            raise ValueError('open() requires mode "r" or "w"')
        if pwd and not isinstance(pwd, bytes):
            raise TypeError("pwd: expected bytes, got %s" % type(pwd).__name__)
        if pwd and (mode == "w"):
            raise ValueError("pwd is only supported for reading files")
        if not self.fp:
            raise ValueError("Attempt to use ZIP archive that was already closed")

        # Make sure we have an info object
        if isinstance(name, P4KInfo):
            # 'name' is already an info object
            zinfo = name
        elif mode == "w":
            zinfo = P4KInfo(name, archive=self)
            zinfo.compress_type = self.compression
            zinfo._compresslevel = self.compresslevel
        else:
            # Get info object for name
            zinfo = self.getinfo(name)

        # if file is in a sub-archive, let that archive handle it
        if zinfo.subinfo is not None:
            return zinfo.archive.open(zinfo.subinfo, mode=mode, pwd=pwd, force_zip64=force_zip64)

        if mode == "w":
            return self._open_to_write(zinfo, force_zip64=force_zip64)

        if self._writing:
            raise ValueError(
                "Can't read from the ZIP file while there "
                "is an open writing handle on it. "
                "Close the writing handle before trying to read."
            )

        # Open for reading:
        self._fileRefCnt += 1
        zef_file = zipfile._SharedFile(
            self.fp,
            zinfo.header_offset,
            self._fpclose,
            self._lock,
            lambda: self._writing,
        )
        try:
            # Skip the file header:
            fheader = zef_file.read(zipfile.sizeFileHeader)
            if len(fheader) != zipfile.sizeFileHeader:
                raise zipfile.BadZipFile("Truncated file header")
            fheader = struct.unpack(zipfile.structFileHeader, fheader)
            if (
                fheader[zipfile._FH_SIGNATURE] != p4kFileHeader
                and fheader[zipfile._FH_SIGNATURE] != zipfile.stringFileHeader
            ):
                raise zipfile.BadZipFile("Bad magic number for file header")

            fname = zef_file.read(fheader[zipfile._FH_FILENAME_LENGTH])
            if fheader[zipfile._FH_EXTRA_FIELD_LENGTH]:
                zef_file.read(fheader[zipfile._FH_EXTRA_FIELD_LENGTH])

            if zinfo.flag_bits & 0x20:
                # Zip 2.7: compressed patched data
                raise NotImplementedError("compressed patched data (flag bit 5)")

            if zinfo.flag_bits & 0x40:
                # strong encryption
                raise NotImplementedError("strong encryption (flag bit 6)")

            if zinfo.flag_bits & 0x800:
                # UTF-8 filename
                fname_str = fname.decode("utf-8")
            else:
                fname_str = fname.decode("cp437")

            if fname_str != zinfo.orig_filename:
                raise zipfile.BadZipFile(
                    "File name in directory %r and header %r differ."
                    % (zinfo.orig_filename, fname)
                )

            zd = None
            if self.key and zinfo.is_encrypted:
                zd = _P4KDecrypter(self.key)

            return P4KExtFile(zef_file, mode, zinfo, zd, True)
        except:
            zef_file.close()
            raise

    def extract_filter(self, file_filter, path=None, ignore_case=False, convert_cryxml=False, quiet=False,
                       search_mode='re', monitor=print):
        self.extractall(path=path, members=self.search(file_filter, ignore_case=ignore_case, mode=search_mode),
                        convert_cryxml=convert_cryxml, quiet=quiet, monitor=monitor)

    def extract(self, member, path=None, pwd=None, convert_cryxml=False, quiet=False, monitor=print):
        """Extract a member from the archive to the current working directory,
           using its full name. Its file information is extracted as accurately
           as possible. `member' may be a filename or a ZipInfo object. You can
           specify a different directory using `path'.
        """
        if path is None:
            path = os.getcwd()
        else:
            path = os.fspath(path)

        return self._extract_member(member, path, pwd, convert_cryxml=convert_cryxml, quiet=quiet, monitor=monitor)

    def extractall(self, path=None, members=None, pwd=None, convert_cryxml=False, quiet=False, monitor=print):
        """Extract all members from the archive to the current working
           directory. `path' specifies a different directory to extract to.
           `members' is optional and must be a subset of the list returned
           by namelist().
        """
        if members is None:
            members = self.namelist()

        if path is None:
            path = os.getcwd()
        else:
            path = os.fspath(path)

        for zipinfo in members:
            self._extract_member(zipinfo, path, pwd, convert_cryxml=convert_cryxml, quiet=quiet, monitor=monitor)

    def search(self, file_filters, ignore_case=True, mode='re'):
        """
        Search the filelist by path

        :param file_filters:
        :param ignore_case:
        :param mode: Method of performing a match. Valid values are:
            `re`:   Compiles `file_filters` into a regular expression - `re.match(filename)`
            `startswith`:  Uses the string `startswith` function - if any(filename.startswith(_) for _ in file_filters)
            `endswith`:  Uses the string `startswith` function - if any(filename.endswith(_) for _ in file_filters)
            `in`:   Performs and `in` check - filename in file_filters
            `in_strip`: Performs an `in` check, but strips the file extension before performing the `in` check
        :return:
        """
        if not isinstance(file_filters, (list, tuple, set)):
            file_filters = [file_filters]
        # normalize path slashes from windows to posix
        file_filters = [_.replace("\\", '/') for _ in file_filters]
        if ignore_case:
            file_filters = [_.lower() for _ in file_filters]

        if mode == 're':
            r = re.compile('|'.join(f'({fnmatch.translate(_)})' for _ in file_filters),
                           flags=re.IGNORECASE if ignore_case else 0)
            return [info for fn, info in self.NameToInfo.items() if r.match(fn)]
        elif mode == 'startswith':
            if ignore_case:
                return [info for fn, info in self.NameToInfoLower.items()
                        if any(fn.startswith(_) for _ in file_filters)]
            else:
                return [info for fn, info in self.NameToInfo.items() if any(fn.startswith(_) for _ in file_filters)]
        elif mode == 'endswith':
            if ignore_case:
                return [info for fn, info in self.NameToInfoLower.items()
                        if any(fn.endswith(_) for _ in file_filters)]
            else:
                return [info for fn, info in self.NameToInfo.items() if any(fn.endswith(_) for _ in file_filters)]
        elif mode == 'in':
            if ignore_case:
                return [info for fn, info in self.NameToInfoLower.items() if fn in file_filters]
            else:
                return [info for fn, info in self.NameToInfo.items() if fn in file_filters]
        elif mode == 'in_strip':
            if ignore_case:
                return [info for fn, info in self.NameToInfoLower.items()
                        if fn.split('.', maxsplit=1)[0] in file_filters]
            else:
                return [info for fn, info in self.NameToInfo.items() if fn.split('.', maxsplit=1)[0] in file_filters]

        raise AttributeError(f'Invalid search mode: {mode}')

    def getinfo(self, name, case_insensitive=True):
        try:
            info = super().getinfo(name)
        except KeyError:
            if not case_insensitive:
                raise
            info = self.NameToInfoLower.get(name.lower())
            if info is None:
                raise KeyError('There is no item named %r in the archive' % name)
        return info

    def save_to(self, member, path, convert_cryxml=False):
        """ Extract a member into `path`. This will no recreate the archive directory structure, it will place the
        extracted file directly into `path` which must exist. Use `extract` """
        return self._extract_member(member, path, pwd=None, convert_cryxml=convert_cryxml, quiet=False, save_to=True)

    def _extract_member(self, member, targetpath, pwd, convert_cryxml=False, quiet=False, save_to=False, monitor=print):
        """Extract the ZipInfo object 'member' to a physical file on the path targetpath. """
        if not isinstance(member, P4KInfo):
            member = self.getinfo(member)

        # TODO: handle not overwriting existing files flag?

        # TODO: change this to use python logging so it can be easily shut off
        # Also convert the file to JSON if it's a CryXmlB file
        if member.filename.split('.', maxsplit=1)[-1].lower() in CRYXMLB_FORMATS and convert_cryxml:
            with self.open(member) as f:
                if f.read(7) == b'CryXmlB':
                    f.seek(0)
                    if save_to:
                        outpath = Path(targetpath) / Path(member.filename).name
                    else:
                        outpath = Path(targetpath) / Path(member.filename)
                    outpath.parent.mkdir(exist_ok=True, parents=True)
                    with outpath.open('w') as t:
                        if self.convert_fmt == 'xml':
                            t.write(pprint_xml_tree(etree_from_cryxml_file(f)))
                        else:
                            json.dump(dict_from_cryxml_file(f), t, indent=4)
                    return str(outpath)

        if not quiet:
            monitor(
                f"{compressor_names[member.compress_type]} | "
                f'{"Crypt" if member.is_encrypted else "Plain"} | {member.filename}'
            )

        if save_to:
            targetpath = Path(targetpath) / Path(member.filename).name
            with self.open(member, pwd=pwd) as source, targetpath.open("wb") as target:
                shutil.copyfileobj(source, target)
        else:
            targetpath = super()._extract_member(member, targetpath, pwd)

        return targetpath


class _ZipFileWithFlexibleFilenames(zipfile.ZipFile):
    """ A regular ZipFile that doesn't enforce filenames perfectly matching the CD filename"""
    def open(self, name, mode="r", pwd=None, *, force_zip64=False):
        if mode not in {"r", "w"}:
            raise ValueError('open() requires mode "r" or "w"')
        if pwd and not isinstance(pwd, bytes):
            raise TypeError("pwd: expected bytes, got %s" % type(pwd).__name__)
        if pwd and (mode == "w"):
            raise ValueError("pwd is only supported for reading files")
        if not self.fp:
            raise ValueError(
                "Attempt to use ZIP archive that was already closed")

        # Make sure we have an info object
        if isinstance(name, zipfile.ZipInfo):
            # 'name' is already an info object
            zinfo = name
        elif mode == 'w':
            zinfo = zipfile.ZipInfo(name)
            zinfo.compress_type = self.compression
            zinfo._compresslevel = self.compresslevel
        else:
            # Get info object for name
            zinfo = self.getinfo(name)

        if mode == 'w':
            return self._open_to_write(zinfo, force_zip64=force_zip64)

        if self._writing:
            raise ValueError("Can't read from the ZIP file while there "
                             "is an open writing handle on it. "
                             "Close the writing handle before trying to read.")

        # Open for reading:
        self._fileRefCnt += 1
        zef_file = zipfile._SharedFile(self.fp, zinfo.header_offset,
                                       self._fpclose, self._lock, lambda: self._writing)
        try:
            # Skip the file header:
            fheader = zef_file.read(zipfile.sizeFileHeader)
            if len(fheader) != zipfile.sizeFileHeader:
                raise zipfile.BadZipFile("Truncated file header")
            fheader = struct.unpack(zipfile.structFileHeader, fheader)
            if fheader[zipfile._FH_SIGNATURE] != zipfile.stringFileHeader:
                raise zipfile.BadZipFile("Bad magic number for file header")

            fname = zef_file.read(fheader[zipfile._FH_FILENAME_LENGTH])
            if fheader[zipfile._FH_EXTRA_FIELD_LENGTH]:
                zef_file.read(fheader[zipfile._FH_EXTRA_FIELD_LENGTH])

            if zinfo.flag_bits & 0x20:
                # Zip 2.7: compressed patched data
                raise NotImplementedError("compressed patched data (flag bit 5)")

            if zinfo.flag_bits & 0x40:
                # strong encryption
                raise NotImplementedError("strong encryption (flag bit 6)")

            # check for encrypted flag & handle password
            is_encrypted = zinfo.flag_bits & 0x1
            if is_encrypted:
                if not pwd:
                    pwd = self.pwd
                if not pwd:
                    raise RuntimeError("File %r is encrypted, password "
                                       "required for extraction" % name)
            else:
                pwd = None

            return zipfile.ZipExtFile(zef_file, mode, zinfo, pwd, True)
        except:
            zef_file.close()
            raise


class SOCPak(_ZipFileWithFlexibleFilenames):
    pass


class Pak(_ZipFileWithFlexibleFilenames):
    pass


# Down here so the can reference local classes
SUB_ARCHIVES = {
    'pak': Pak,
    'socpak': SOCPak
}


if __name__ == "__main__":
    PTU_DIR = "D:/Games/RSI/StarCitizen/PTU/"
    p4k = P4KFile(os.path.join(PTU_DIR, "Data.p4k"))
    # print(p4k.filelist)
    # p4k.extractall(PTU_DIR)
    p4k.extract_filter("*.xml", PTU_DIR)
    # p4k.extract('Data/Scripts/Loadouts/Vehicles/Default_Loadout_AEGS_Retaliator.xml', PTU_DIR)
    # p4k.extract('Data/Objects/buildingsets/hangar/asteroid/textures/uee_asteroid_hangar_plasticsheet_01_diff.dds.6', PTU_DIR)
