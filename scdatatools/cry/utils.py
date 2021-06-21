import sys
import ctypes


class StructureWithEnums:
    """Add missing enum feature to ctypes Structures.
    """
    _map = {}

    def __getattribute__(self, name):
        _map = ctypes.Structure.__getattribute__(self, '_map')

        value = ctypes.Structure.__getattribute__(self, name)
        if name in _map:
            classes = _map[name]
            if not isinstance(classes, (list, tuple)):
                classes = [classes]
            for enumClass in classes:
                try:
                    if isinstance(value, ctypes.Array):
                        return [enumClass(x) for x in value]
                    else:
                        return enumClass(value)
                except ValueError:
                    pass
            else:
                sys.stderr.write(f'\n{value} is not valid for any of the types "{repr(classes)}"\n')
        return value

    def __str__(self):
        result = []
        result.append("struct {0} {{".format(self.__class__.__name__))
        for field in self._fields_:
            attr, attrType = field
            if attr in self._map:
                attrType = repr(self._map[attr]) if len(self._map[attr]) > 1 else self._map[attr][0].__name__
            else:
                attrType = attrType.__name__
            value = getattr(self, attr)
            result.append("    {0} [{1}] = {2!r};".format(attr, attrType, value))
        result.append("};")
        return '\n'.join(result)

    __repr__ = __str__


class FileHeaderStructure(StructureWithEnums):
    def __getattribute__(self, item):
        val = super().__getattribute__(item)
        if item == 'signature':
            return val.to_bytes(4, 'little')
        return val
