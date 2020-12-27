import sys
import zlib
import json
import xxhash
from pathlib import Path
from collections import defaultdict
from xml.etree import ElementTree


def xxhash32_file(file_or_path):
    if hasattr(file_or_path, 'read'):
        fp = file_or_path
        _close = False
    else:
        _close = True
        if not isinstance(file_or_path, Path):
            file_or_path = Path(file_or_path)
        fp = file_or_path.open('rb')

    fp.seek(0)
    hash = xxhash.xxh32()
    while True:
        s = fp.read(8096)
        if not s:
            break
        hash.update(s)

    if _close:
        fp.close()

    return hash.hexdigest()


def xxhash32(data):
    hash = xxhash.xxh32()
    hash.update(data)
    return hash.hexdigest()


def crc32(fileName_or_path):
    if not isinstance(fileName_or_path, Path):
        fileName_or_path = Path(fileName_or_path)

    with fileName_or_path.open('rb') as fh:
        hash = 0
        while True:
            s = fh.read(65536)
            if not s:
                break
            hash = zlib.crc32(s, hash)
        return "%08X" % (hash & 0xFFFFFFFF)


def get_size(obj, seen=None):
    """Recursively finds size of objects"""
    size = sys.getsizeof(obj)
    if seen is None:
        seen = set()
    obj_id = id(obj)
    if obj_id in seen:
        return 0
    # Important mark as seen *before* entering recursion to gracefully handle
    # self-referential objects
    seen.add(obj_id)
    if isinstance(obj, dict):
        size += sum([get_size(v, seen) for v in obj.values()])
        size += sum([get_size(k, seen) for k in obj.keys()])
    elif hasattr(obj, '__dict__'):
        size += get_size(obj.__dict__, seen)
    elif hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes, bytearray)):
        size += sum([get_size(i, seen) for i in obj])
    return size


def version_from_id_file(id_file) -> (dict, str):
    opened = False
    if isinstance(id_file, str):
        opened = True
        id_file = open(id_file, 'r')

    version_data = {}
    version_label = ''
    try:
        version_data = json.loads(id_file.read()).get("Data", {})
        branch = version_data.get("Branch", None)
        version = version_data.get("RequestedP4ChangeNum", None)
        version_label = f"{branch}-{version}"
    except Exception as e:
        sys.stderr.write(
            f"Warning: Unable to determine version of P4K file, missing or corrupt c_win_shader.id"
        )
    finally:
        if opened:
            id_file.close()
    return version_data, version_label


# etree<->dict conversions from
# from https://stackoverflow.com/a/10076823


def etree_to_dict(t: ElementTree.ElementTree) -> dict:
    """ Convert the given ElementTree `t` to an dict following the following XML to JSON specification:
    https://www.xml.com/pub/a/2006/05/31/converting-between-xml-and-json.html
    """
    if isinstance(t, ElementTree.ElementTree):
        t = t.getroot()

    d = {t.tag: {} if hasattr(t, "attrib") else None}
    children = list(t)
    if children:
        dd = defaultdict(list)
        for dc in map(etree_to_dict, children):
            for k, v in dc.items():
                dd[k].append(v)
        d = {t.tag: {k: v[0] if len(v) == 1 else v for k, v in dd.items()}}
    if t.attrib:
        d[t.tag].update(("@" + k, v) for k, v in t.attrib.items())
    if t.text:
        text = t.text.strip()
        if children or t.attrib:
            if text:
                d[t.tag]["#text"] = text
        else:
            d[t.tag] = text
    return d


def dict_to_etree(d: dict) -> ElementTree:
    """ Convert the given dict `d` to an ElementTree following the following XML to JSON specification:
    https://www.xml.com/pub/a/2006/05/31/converting-between-xml-and-json.html
    """

    def _to_etree(d, root):
        if not d:
            pass
        elif isinstance(d, str):
            root.text = d
        elif isinstance(d, dict):
            for k, v in d.items():
                if k.startswith("#"):
                    root.text = str(v)
                elif k.startswith("@"):
                    root.set(k[1:], str(v))
                elif isinstance(v, list):
                    sub = ElementTree.SubElement(root, str(k))
                    for e in v:
                        _to_etree(e, sub)
                elif isinstance(v, dict):
                    _to_etree(v, ElementTree.SubElement(root, str(k)))
                elif isinstance(d, bool):
                    root.text = str(int(d))
                else:
                    if isinstance(v, bool):
                        v = int(v)
                    root.set(str(k), str(v))
                    # _to_etree(v, ElementTree.SubElement(root, k))
        elif isinstance(d, bool):
            root.text = str(int(d))
        else:
            root.text = str(d)
            # assert d == "invalid type", (type(d), d)

    assert isinstance(d, dict) and len(d) == 1
    tag, body = next(iter(d.items()))
    node = ElementTree.Element(tag)
    _to_etree(body, node)
    return ElementTree.ElementTree(node)
