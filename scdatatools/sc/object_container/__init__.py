import logging
import typing
from pathlib import Path
from typing import TYPE_CHECKING
from functools import cached_property

from scdatatools.p4k import P4KInfo
from scdatatools.utils import norm_path
from scdatatools.engine.chunkfile import chunks
from scdatatools.engine.chunkfile import ChunkFile
from scdatatools.engine.cryxml import etree_from_cryxml_file, dict_from_cryxml_file

if TYPE_CHECKING:
    from scdatatools.sc import StarCitizen


logger = logging.getLogger(__name__)


class StreamingObjectContainer:
    def __init__(
        self, soc_info: P4KInfo, object_container: "ObjectContainer", attrs: dict = None
    ):
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
            cid: chunk
            for cid, chunk in self.chunks.items()
            if isinstance(chunk, chunks.JSONChunk)
        }


class ObjectContainer:
    def __init__(self, sc: "StarCitizen", socpak: "P4KInfo"):
        self._sc = sc
        self._pak_base = socpak.filename.replace(".socpak", "")
        self.socpak = socpak
        self.children = {}
        self.socs = {}

        self._p4k_path = Path(self.socpak.filename)
        self._pak_name = self._p4k_path.stem
        self._load_soc_xml(
            self._p4k_path.parent / self._p4k_path.stem / f"{self._p4k_path.stem}.xml"
        )

        base_soc_info = self._sc.p4k.getinfo(
            (
                self._p4k_path.parent
                / self._p4k_path.stem
                / f"{self._p4k_path.stem}.soc"
            ).as_posix()
        )
        if base_soc_info:
            base_soc = StreamingObjectContainer(base_soc_info, self)
            if base_soc.name not in self.socs:
                self.socs[base_soc.name] = base_soc

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
                child = dict(**child_elem.attrib)

                child["tags"] = [
                    self._sc.tag_database.tags_by_guid[tag_id]
                    for tag_id in child.get("tags", "").split(",")
                    if tag_id in self._sc.tag_database.tags_by_guid
                ]

                try:
                    ent_info = self._sc.p4k.getinfo(
                        f'{self._pak_base}/entdata/{child["guid"]}.entxml'
                    )
                    child["entdata"] = dict_from_cryxml_file(ent_info.open())["Entity"]
                except KeyError:
                    child["entdata"] = {}

                cur_parent[child["guid"]] = child
                _parse_children(
                    cur_parent[child["guid"]].setdefault("children", {}),
                    child_elem.find("./ChildObjectContainers"),
                )

        _parse_children(self.children, oc_etree.find("./ChildObjectContainers"))

        for soc in oc_etree.findall(".//OC"):
            self._load_soc(soc)

    def _load_soc(self, soc_etree):
        attrs = dict(**soc_etree.attrib)
        soc_path = f"{self._pak_base}/{norm_path(attrs['name']).lower().replace(f'{self._pak_name}/', '')}"
        try:
            soc_info = self._sc.p4k.getinfo(soc_path)
        except KeyError:
            logger.error(
                f'soc "{attrs["name"]}" not found for object container {self.socpak.filename}'
            )
            return
        soc = StreamingObjectContainer(soc_info, self, attrs)
        self.socs[soc.name] = soc


class ObjectContainerManager:
    def __init__(self, sc: "StarCitizen"):
        self.sc = sc
        self.object_containers = {}

    def load_all_containers(self):
        for socpak_info in self.sc.p4k.search("*.socpak"):
            try:
                self.load_socpak(socpak_info)
            except Exception as e:
                logger.exception(
                    f"Could not load socpak {socpak_info.filename}", exc_info=e
                )

    def load_socpak(self, socpak: typing.Union[P4KInfo, str]) -> ObjectContainer:
        if not isinstance(socpak, P4KInfo):
            socpak = norm_path(
                f'{"" if socpak.lower().startswith("data") else "data/"}{socpak}'
            )
            socpak = self.sc.p4k.getinfo(socpak)

        if socpak.filename in self.object_containers:
            return self.object_containers[socpak.filename]

        oc = ObjectContainer(self.sc, socpak)
        self.object_containers[socpak.filename] = oc
        return oc
