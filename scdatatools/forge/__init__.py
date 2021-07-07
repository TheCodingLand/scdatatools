__all__ = ["DataCoreBinary"]

import os
import sys
import json
import mmap
import ctypes
import fnmatch
import typing
from pathlib import Path
from collections import defaultdict

# from benedict import benedict

from scdatatools.forge import dftypes
from scdatatools.forge.utils import read_and_seek
from scdatatools.forge.dftypes.enums import DataTypes
from scdatatools.utils import dict_to_etree
from scdatatools.cry.cryxml.utils import pprint_xml_tree


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
    def __init__(self, filename_or_data: typing.Union[str, Path, bytes, bytearray]):
        if isinstance(filename_or_data, bytes):
            self.raw_data = bytearray(filename_or_data)
        elif isinstance(filename_or_data, bytearray):
            self.raw_data = filename_or_data
        else:
            filename_or_data = Path(filename_or_data)
            if not filename_or_data.is_file():
                raise ValueError(f'Expected bytes or filename, not: {filename_or_data}')
            with filename_or_data.open('rb') as f:
                self.raw_data = f.read()
        self.raw_data = memoryview(self.raw_data)

        # used to track position while reading the header
        offset = 0

        def _read_and_seek(data_type):
            nonlocal offset, self
            r = data_type.from_buffer(self.raw_data, offset)
            setattr(r, "_dcb", self)
            offset += ctypes.sizeof(r)
            return r

        self.header = _read_and_seek(dftypes.DataCoreHeader)
        self.structure_definitions = _read_and_seek(
            dftypes.StructureDefinition * self.header.structure_definition_count
        )
        self.property_definitions = _read_and_seek(
            dftypes.PropertyDefinition * self.header.property_definition_count
        )
        self.enum_definitions = _read_and_seek(
            dftypes.EnumDefinition * self.header.enum_definition_count
        )
        if self.header.version >= 5:
            self.data_mapping_definitions = _read_and_seek(
                dftypes.DataMappingDefinition32 * self.header.data_mapping_definition_count
            )
        else:
            self.data_mapping_definitions = _read_and_seek(
                dftypes.DataMappingDefinition16 * self.header.data_mapping_definition_count
            )
        self.records = _read_and_seek(dftypes.Record * self.header.record_definition_count)
        self.values = {
            DataTypes.Int8: _read_and_seek(ctypes.c_int8 * self.header.int8_count),
            DataTypes.Int16: _read_and_seek(ctypes.c_int16 * self.header.int16_count),
            DataTypes.Int32: _read_and_seek(ctypes.c_int32 * self.header.int32_count),
            DataTypes.Int64: _read_and_seek(ctypes.c_int64 * self.header.int64_count),
            DataTypes.UInt8: _read_and_seek(ctypes.c_uint8 * self.header.uint8_count),
            DataTypes.UInt16: _read_and_seek(ctypes.c_uint16 * self.header.uint16_count),
            DataTypes.UInt32: _read_and_seek(ctypes.c_uint32 * self.header.uint32_count),
            DataTypes.UInt64: _read_and_seek(ctypes.c_uint64 * self.header.uint64_count),
            DataTypes.Boolean: _read_and_seek(ctypes.c_bool * self.header.boolean_count),
            DataTypes.Float: _read_and_seek(ctypes.c_float * self.header.float_count),
            DataTypes.Double: _read_and_seek(ctypes.c_double * self.header.double_count),
            DataTypes.GUID: _read_and_seek(dftypes.GUID * self.header.guid_count),
            DataTypes.StringRef: _read_and_seek(dftypes.StringReference * self.header.string_count),
            DataTypes.Locale: _read_and_seek(dftypes.LocaleReference * self.header.locale_count),
            DataTypes.EnumChoice: _read_and_seek(dftypes.EnumChoice * self.header.enum_count),
            DataTypes.StrongPointer: _read_and_seek(dftypes.StrongPointer * self.header.strong_value_count),
            DataTypes.WeakPointer: _read_and_seek(dftypes.WeakPointer * self.header.weak_value_count),
            DataTypes.Reference: _read_and_seek(dftypes.Reference * self.header.reference_count),
            DataTypes.EnumValueName: _read_and_seek(dftypes.StringReference * self.header.enum_option_name_count),
        }

        self.text_offset = offset
        offset += self.header.text_length

        self.structure_instances = {}
        for mapping in self.data_mapping_definitions:
            struct_def = self.structure_definitions[mapping.structure_index]
            struct_size = struct_def.calculated_data_size
            for i in range(mapping.structure_count):
                # TODO: This adds a significant amount of time (~3150%) to processing the dataforge. Either significantly
                #       increase this, or delay loading
                self.structure_instances.setdefault(mapping.structure_index, []).append(
                    (offset, struct_size)
                    # dftypes.StructureInstance(
                    #     self,
                    #     self.raw_data[offset: offset + struct_size],
                    #     struct_def,
                    # )
                )
                offset += struct_size
        assert offset == len(self.raw_data)

        self.records_by_guid = {r.id.value: r for r in self.records}
        # self._records_by_path = benedict(keypath_separator='/')

    def get_structure_instance(self, structure_index, instance):
        if not isinstance(self.structure_instances[structure_index][instance], dftypes.StructureInstance):
            offset, size = self.structure_instances[structure_index][instance]
            self.structure_instances[structure_index][instance] = dftypes.StructureInstance(
                self, self.raw_data[offset:offset+size], self.structure_definitions[structure_index]
            )
        return self.structure_instances[structure_index][instance]

    def string_for_offset(self, offset: int, encoding="UTF-8") -> str:
        try:
            if offset >= self.header.text_length:
                raise IndexError(f'Text offset "{offset}" is out of range')

            end = self.raw_data.obj.index(0x00, self.text_offset + offset, self.text_offset + self.header.text_length)
            return bytes(self.raw_data[self.text_offset + offset:end]).decode(encoding)
        except ValueError:
            sys.stderr.write(f"Invalid string offset: {offset}")
            return ""

    def record_to_dict(self, record, depth=100):
        d = {}
        refd = set()

        def _add_props(base, r, cur_depth):
            rid = ''
            if hasattr(r, 'id'):
                base['__id'] = r.id.value
                rid = r.id.value
            if hasattr(r, 'filename'):
                base['__path'] = r.filename
            if getattr(r, 'structure_definition', None) is not None:
                if r.structure_definition.parent is not None:
                    base['__type'] = r.structure_definition.parent.name
                    base['__polymorphicType'] = r.structure_definition.name
                else:
                    base['__type'] = r.structure_definition.name
            if hasattr(r, 'instance_index'):
                rid = f'{r.name}:{r.instance_index}'
            if rid:
                refd.add(rid)
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
                        pid = ''
                        if hasattr(p, 'id'):
                            pid = p.id.value
                        elif hasattr(p, 'instance_index'):
                            pid = f'{p.name}:{p.instance_index}'
                        if cur_depth > 0:  # NextState/parent tends to lead to infinite loops
                            if pname.lower() in ['nextstate', 'parent'] or (pid and pid in refd):
                                nextdepth = 0
                            else:
                                nextdepth = cur_depth - 1
                            _add_props(b, p, nextdepth)
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
        """ Search the dco by filename """
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

    # @property
    # def records_by_path(self):
    #     if not self._records_by_path:
    #         for r in self.records:
    #             path = r.filename
    #             if path in self._records_by_path:
    #                 if not isinstance(self._records_by_path[path], list):
    #                     self._records_by_path[path] = [self._records_by_path[path]]
    #                 self._records_by_path[path].append(r)
    #             else:
    #                 self._records_by_path[path] = r
    #     return self._records_by_path
