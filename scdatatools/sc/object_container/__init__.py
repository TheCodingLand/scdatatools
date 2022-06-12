import logging
import typing
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from scdatatools.engine.chunkfile import ChunkFile
from scdatatools.engine.chunkfile import chunks
from scdatatools.engine.cryxml import etree_from_cryxml_file, dict_from_cryxml_file
from scdatatools.engine.model_utils import vector_from_csv, quaternion_from_csv
from scdatatools.p4k import P4KInfo
from scdatatools.utils import norm_path

if TYPE_CHECKING:
    from scdatatools.sc import StarCitizen

logger = logging.getLogger(__name__)


class StreamingObjectContainer:
    def __init__(self, soc_info: P4KInfo, object_container: "ObjectContainer", attrs: dict = None):
        self.name = Path(soc_info.filename).name
        self.attrs = attrs or {}
        self.object_container = object_container
        self.soc_info = soc_info

    @property
    def chunks(self):
        return self._chcr.chunks

    @cached_property
    def _chcr(self):
        return ChunkFile(self.soc_info)

    @cached_property
    def included_objects(self):
        return {
            cid: chunk
            for cid, chunk in self.chunks.items()
            if isinstance(chunk, chunks.IncludedObjects)
        }

    @cached_property
    def cryxml_chunks(self):
        return {
            cid: chunk
            for cid, chunk in self.chunks.items()
            if isinstance(chunk, chunks.CryXMLBChunk)
        }

    @cached_property
    def json_chunks(self):
        return {
            cid: chunk for cid, chunk in self.chunks.items() if isinstance(chunk, chunks.JSONChunk)
        }


class ObjectContainerInstance:
    __INSTANCE_ARGS__ = {
        "external": "external",
        "entityName": "entity_name",
        "label": "label",
        "class": "container_class",
        "tags": "tags",
        "visible": "visible",
        "guid": "guid",
        "pos": "position",
        "rot": "rotation",
        "hasAdditionalData": "has_additional_data",
    }

    def __init__(
        self,
        sc: "StarCitizen",
        name: str,
        root: "ObjectContainer",
        parent: typing.Union["ObjectContainer", "ObjectContainerInstance"],
        entdata: dict = None,
        **kwargs,
    ):
        self._sc = sc
        self.name = name
        self.root = root
        self.parent = parent
        self._attrs = {}
        self.entdata = entdata or {}
        try:
            self.container = self._sc.oc_manager.load_socpak(name)
            self.container.instances.setdefault(root.name, []).append(self)
        except KeyError:
            self.container = None

        for k, v in ObjectContainerInstance.__INSTANCE_ARGS__.items():
            if k in kwargs:
                self._attrs[v] = kwargs[k]
                setattr(self, v, kwargs[k])
            elif v in kwargs:
                self._attrs[v] = kwargs[v]  # from a duplicated instance
                setattr(self, v, kwargs[v])

        self.label = self._attrs.get("label", self.entdata.get("@Name", Path(self.name).stem))
        self.position = vector_from_csv(self._attrs.get("position", "0,0,0"))
        self.rotation = quaternion_from_csv(self._attrs.get("rotation", "1,0,0,0"))
        if "entity_name" not in self._attrs:
            self._attrs["entity_name"] = ""
            self.entity_name = ""

        if isinstance(parent, ObjectContainerInstance):
            self.global_position = parent.global_position + self.position
            self.global_rotation = (
                parent.global_rotation * self.rotation
            )  # TODO: is this actually a thing?
        else:
            self.global_position = self.position
            self.global_rotation = self.rotation

        self._children_loaded = False
        self._children_by_id: dict[str, ObjectContainerInstance] = {}
        self._children: dict[str, ObjectContainerInstance] = {}

    @property
    def socpak(self):
        if self.container is not None:
            return self.container.socpak
        return None

    def __delete__(self, instance):
        if self.container is not None:
            self.container.instances[self.root.name].remove(self)

    def __getitem__(self, item):
        return self.children[item]

    def duplicate(self, parent: typing.Union["ObjectContainer", "ObjectContainerInstance"]):
        return ObjectContainerInstance(
            sc=self._sc, name=self.name, root=parent.root, parent=parent, **self._attrs
        )

    def add_child(self, child_id, child):
        self._children_by_id[child_id] = child

    def _ensure_children_loaded(self):
        if not self._children_loaded:
            if self.container is None:
                children_by_id = {}
            else:
                children_by_id = {
                    child_id: child.duplicate(self)
                    for child_id, child in self.container.children_by_id.items()
                    if child_id not in self._children
                }
            children_by_id.update(self._children_by_id)
            self._children_by_id = children_by_id
            self._children = {child.label: child for child in children_by_id.values()}
            self._children_loaded = True

    @property
    def children(self):
        self._ensure_children_loaded()
        return self._children

    @property
    def children_by_id(self):
        self._ensure_children_loaded()
        return self._children_by_id

    def as_dict(self) -> dict:
        attrs = self._attrs.copy()
        attrs.update(
            {
                "name": self.name,
                "container": self.container,
                "global_position": self.global_position,
                "global_rotation": self.global_rotation,
                "position": self.position,
                "rotation": self.rotation,
                "parent": self.parent,
                "root": self.root,
            }
        )
        return attrs

    def __repr__(self):
        return (
            f"<ObjectContainerInstance {self.name} parent:{self.parent.name} root:{self.root.name}>"
        )


class ObjectContainer:
    def __init__(self, sc: "StarCitizen", socpak: "P4KInfo"):
        self._sc = sc
        self._pak_base = socpak.filename.replace(".socpak", "")

        self.name = socpak.filename
        self.socpak = socpak

        self.instances = {}
        self.children: typing.Dict[str, ObjectContainerInstance] = {}
        self.children_by_id: typing.Dict[str, ObjectContainerInstance] = {}
        self.socs = {}

        self._p4k_path = Path(self.socpak.filename)
        self._pak_name = self._p4k_path.stem
        self._load_soc_xml(
            self._p4k_path.parent / self._p4k_path.stem / f"{self._p4k_path.stem}.xml"
        )

        base_soc_info = self._sc.p4k.getinfo(
            (self._p4k_path.parent / self._p4k_path.stem / f"{self._p4k_path.stem}.soc").as_posix()
        )
        if base_soc_info:
            base_soc = StreamingObjectContainer(base_soc_info, self)
            if base_soc.name not in self.socs:
                self.socs[base_soc.name] = base_soc

    def as_dict(self) -> dict:
        return self.attrs

    def add_child(self, child_id: str, child: ObjectContainerInstance):
        if child.label in self.children:
            self.children[child_id] = child
        else:
            self.children[child.label] = child
        self.children_by_id[child_id] = child

    def __getitem__(self, item):
        return self.children[item]

    def _load_soc_xml(self, soc_xml_path):
        soc_xml = self._sc.p4k.NameToInfoLower.get(soc_xml_path.as_posix().lower())
        if soc_xml is None:
            raise KeyError(f"Could not find xml for socpak: {soc_xml_path}")
        oc_etree = etree_from_cryxml_file(soc_xml.open())

        self.attrs = dict(**oc_etree.getroot().attrib)

        self.tags = [
            self._sc.tag_database.tags_by_guid[tag_id]
            for tag in oc_etree.findall(".//Tag")
            if (tag_id := tag.get("TagId")) in self._sc.tag_database.tags_by_guid
        ]

        def _parse_children(cur_parent, child_containers):
            if child_containers is None:
                return
            for child_elem in child_containers.findall("./Child"):
                child_attrs = dict(**child_elem.attrib)

                child_attrs["tags"] = [
                    self._sc.tag_database.tags_by_guid[tag_id]
                    for tag_id in child_attrs.get("tags", "").split(",")
                    if tag_id in self._sc.tag_database.tags_by_guid
                ]

                try:
                    ent_info = self._sc.p4k.getinfo(
                        f'{self._pak_base}/entdata/{child_attrs["guid"]}.entxml'
                    )
                    child_attrs["entdata"] = dict_from_cryxml_file(ent_info.open())["Entity"]
                except KeyError:
                    child_attrs["entdata"] = {}

                child = ObjectContainerInstance(
                    self._sc, child_attrs.pop("name"), root=self, parent=cur_parent, **child_attrs
                )
                cur_parent.add_child(child_attrs["guid"], child)
                _parse_children(child, child_elem.find("./ChildObjectContainers"))

        _parse_children(self, oc_etree.find("./ChildObjectContainers"))

        for soc in oc_etree.findall(".//OC"):
            self._load_soc(soc)

    def _load_soc(self, soc_etree):
        attrs = dict(**soc_etree.attrib)
        soc_path = (
            f"{self._pak_base}/{norm_path(attrs['name']).lower().replace(f'{self._pak_name}/', '')}"
        )
        try:
            soc_info = self._sc.p4k.getinfo(soc_path)
        except KeyError:
            logger.error(
                f'soc "{attrs["name"]}" not found for object container {self.socpak.filename}'
            )
            return
        soc = StreamingObjectContainer(soc_info, self, attrs)
        self.socs[soc.name] = soc

    def __repr__(self):
        return f"<ObjectContainer {self.name}>"


class ObjectContainerManager:
    def __init__(self, sc: "StarCitizen"):
        self.sc = sc
        self.object_containers = {}

    def load_all_containers(self):
        for socpak_info in self.sc.p4k.search("*.socpak"):
            try:
                self.load_socpak(socpak_info)
            except Exception as e:
                logger.exception(f"Could not load socpak {socpak_info.filename}", exc_info=e)

    def load_socpak(self, socpak: typing.Union[P4KInfo, str]) -> ObjectContainer:
        if not isinstance(socpak, P4KInfo):
            socpak = norm_path(f'{"" if socpak.lower().startswith("data") else "data/"}{socpak}')
            socpak = self.sc.p4k.getinfo(socpak)

        if socpak.filename in self.object_containers:
            return self.object_containers[socpak.filename]

        oc = ObjectContainer(self.sc, socpak)
        self.object_containers[socpak.filename] = oc
        return oc
