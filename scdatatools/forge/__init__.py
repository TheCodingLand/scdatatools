__all__ = ["DataCoreBinary"]

import os
import sys
import json
import mmap
import ctypes
import fnmatch
from io import IOBase, FileIO
from collections import defaultdict

from scdatatools.forge import dftypes
from scdatatools.forge.utils import read_and_seek
from scdatatools.forge.dftypes.enums import DataTypes
from scdatatools.utils import dict_to_etree
from scdatatools.cryxml.utils import pprint_xml_tree


class DataCoreBinaryMMap(mmap.mmap):
    def __new__(cls, filename_or_file, *args, **kwargs):
        if hasattr(filename_or_file, 'fileno'):
            _ = filename_or_file
        else:
            _ = open(filename_or_file, "rb+")
        instance = super().__new__(cls, fileno=_.fileno(), length=0, *args, **kwargs)
        instance.file = _
        return instance

    def close(self, *args, **kwargs):
        try:
            super().close(*args, **kwargs)
        finally:
            self.file.close()

    def seek(self, *args, **kwargs):
        # make this work like normal seek() where you get the offset after the seek
        super().seek(*args, **kwargs)
        return self.tell()


class DataCoreBinary:
    def __init__(self, filename_or_data):
        if isinstance(filename_or_data, str):
            self.raw_data = DataCoreBinaryMMap(filename_or_data)
        else:
            self.raw_data = filename_or_data

        self.header = read_and_seek(self, dftypes.DataCoreHeader)
        self.structure_definitions = read_and_seek(
            self, dftypes.StructureDefinition * self.header.structure_definition_count
        )
        self.property_definitions = read_and_seek(
            self, dftypes.PropertyDefinition * self.header.property_definition_count
        )
        self.enum_definitions = read_and_seek(
            self, dftypes.EnumDefinition * self.header.enum_definition_count
        )
        if self.header.version >= 5:
            self.data_mapping_definitions = read_and_seek(
                self, dftypes.DataMappingDefinition32 * self.header.data_mapping_definition_count
            )
        else:
            self.data_mapping_definitions = read_and_seek(
                self, dftypes.DataMappingDefinition16 * self.header.data_mapping_definition_count
            )
        self.records = read_and_seek(
            self, dftypes.Record * self.header.record_definition_count
        )
        self.values = {
            DataTypes.Int8: read_and_seek(self, ctypes.c_int8 * self.header.int8_count),
            DataTypes.Int16: read_and_seek(
                self, ctypes.c_int16 * self.header.int16_count
            ),
            DataTypes.Int32: read_and_seek(
                self, ctypes.c_int32 * self.header.int32_count
            ),
            DataTypes.Int64: read_and_seek(
                self, ctypes.c_int64 * self.header.int64_count
            ),
            DataTypes.UInt8: read_and_seek(
                self, ctypes.c_uint8 * self.header.uint8_count
            ),
            DataTypes.UInt16: read_and_seek(
                self, ctypes.c_uint16 * self.header.uint16_count
            ),
            DataTypes.UInt32: read_and_seek(
                self, ctypes.c_uint32 * self.header.uint32_count
            ),
            DataTypes.UInt64: read_and_seek(
                self, ctypes.c_uint64 * self.header.uint64_count
            ),
            DataTypes.Boolean: read_and_seek(
                self, ctypes.c_bool * self.header.boolean_count
            ),
            DataTypes.Float: read_and_seek(
                self, ctypes.c_float * self.header.float_count
            ),
            DataTypes.Double: read_and_seek(
                self, ctypes.c_double * self.header.double_count
            ),
            DataTypes.GUID: read_and_seek(self, dftypes.GUID * self.header.guid_count),
            DataTypes.StringRef: read_and_seek(
                self, dftypes.StringReference * self.header.string_count
            ),
            DataTypes.Locale: read_and_seek(
                self, dftypes.LocaleReference * self.header.locale_count
            ),
            DataTypes.EnumChoice: read_and_seek(
                self, dftypes.EnumChoice * self.header.enum_count
            ),
            DataTypes.StrongPointer: read_and_seek(
                self, dftypes.StrongPointer * self.header.strong_value_count
            ),
            DataTypes.WeakPointer: read_and_seek(
                self, dftypes.WeakPointer * self.header.weak_value_count
            ),
            DataTypes.Reference: read_and_seek(
                self, dftypes.Reference * self.header.reference_count
            ),
            DataTypes.EnumValueName: read_and_seek(
                self, dftypes.StringReference * self.header.enum_option_name_count
            ),
        }

        self.text = memoryview(
            self.raw_data[
                self.raw_data.tell(): self.raw_data.tell() + self.header.text_length
            ]
        )
        self.raw_data.seek(self.header.text_length, os.SEEK_CUR)

        self.structure_instances = defaultdict(list)
        for mapping in self.data_mapping_definitions:
            struct_def = self.structure_definitions[mapping.structure_index]
            struct_size = struct_def.calculated_data_size
            for i in range(mapping.structure_count):
                offset = self.raw_data.tell()
                self.structure_instances[mapping.structure_index].append(
                    dftypes.StructureInstance(
                        self,
                        memoryview(self.raw_data[offset: offset + struct_size]),
                        struct_def,
                    )
                )
                self.raw_data.seek(struct_size, os.SEEK_CUR)
        assert self.raw_data.tell() == len(self.raw_data)

        self.records_by_guid = {r.id.value: r for r in self.records}

    def string_for_offset(self, offset: int, encoding="UTF-8") -> str:
        try:
            end = self.text.obj.index(0x00, offset)
            return bytes(self.text[offset:end]).decode(encoding)
        except ValueError:
            sys.stderr.write(f"Invalid string offset: {offset}")
            return ""

    def record_to_dict(self, record, depth=100):
        d = {}

        def _add_props(base, r, cur_depth):
            if hasattr(r, 'id'):
                base['__id'] = r.id.value
            if hasattr(r, 'filename'):
                base['__path'] = r.filename
            if getattr(r, 'structure_definition', None) is not None:
                if r.structure_definition.parent is not None:
                    base['__type'] = r.structure_definition.parent.name
                    base['__polymorphicType'] = r.structure_definition.name
                else:
                    base['__type'] = r.structure_definition.name
            for name, prop in r.properties.items():
                if isinstance(prop, dftypes.Reference) and prop.value.value in self.records_by_guid:
                    prop = self.records_by_guid[prop.value.value]

                def _handle_prop(p, pname=''):
                    if isinstance(
                            p,
                            (
                                    dftypes.StructureInstance,
                                    dftypes.ClassReference,
                                    dftypes.Record,
                                    dftypes.StrongPointer,
                            ),
                    ):
                        b = {}
                        if cur_depth > 0:  # NextState/parent tends to lead to infinite loops
                            _add_props(b, p, cur_depth - 1 if pname.lower() not in ['nextstate', 'parent'] else 0)
                        else:
                            if hasattr(b, 'properties'):
                                b = [str(_) for _ in prop.properties]
                            else:
                                b = [str(_) for _ in prop] if isinstance(prop, list) else str(prop)
                        return b
                    else:
                        return getattr(p, 'value', p)

                if isinstance(prop, list):
                    base[name] = [
                        {p.name: _handle_prop(p, p.name)} if hasattr(p, 'name') else _handle_prop(p)
                        for p in prop
                    ]
                else:
                    base[name] = _handle_prop(prop, name)

        _add_props(d, record, depth)
        return d

    def record_to_etree(self, record, depth=100):
        return dict_to_etree({f'{record.type}.{record.name}': self.record_to_dict(record, depth)})

    def dump_record_xml(self, record, indent=4, *args, **kwargs):
        return pprint_xml_tree(self.record_to_etree(record), indent)

    def dump_record_json(self, record, indent=4, *args, **kwargs):
        return json.dumps(self.record_to_dict(record, *args, **kwargs), indent=indent, default=str, sort_keys=True)

    def search_filename(self, file_filter, ignore_case=True):
        """ Search the records by filename """
        file_filter = "/".join(
            file_filter.split("\\")
        )  # normalize path slashes from windows to posix
        if ignore_case:
            file_filter = file_filter.lower()
            return [
                _
                for _ in self.records
                if fnmatch.fnmatch(_.filename.lower(), file_filter)
            ]
        return [_ for _ in self.records if fnmatch.fnmatchcase(_.filename, file_filter)]
