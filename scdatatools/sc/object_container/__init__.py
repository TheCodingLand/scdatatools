import logging
import typing
from decimal import Decimal
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


def reduce_pos(point):
    # maximum place value discovered was 11 with shortest non-zero value as 4
    # for i in points_at_size[14]: print(len(str(i[0][0]).split('.')[0].replace('-',''))) for x & for i in points_at_size[14]: print(len(str(i[0][1]).split('.')[0].replace('-',''))) for y
    print("point in:", point)

    def shift_reduce(pos):
        shift_delta = 2
        print("shift reduce pos :", pos)
        shift = len(str(pos).split('.')[0].replace('-', '')) / shift_delta
        print("shift value: ", shift)
        return float(Decimal.shift(Decimal(round(pos)), round(-shift)))

    np = []
    for i in point:
        print("iteration of point :", i)
        if i != 0.0:
            if i < 0.0:
                i = shift_reduce(i)
            elif i > 0.0:
                i = shift_reduce(i)
        print("value of p returned:", i)
        np.append(i)
    print("point out:", (np[0], np[1], np[2]))

    return (np[0], np[1], np[2])


def reduce_size(object_container):
    r = 0.0033
    object_container.size = object_container.size * r
    if object_container.size < 0.0:
        object_container.size = object_container.size / (r * 10)


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
        # "visible": "visible",     # removed in 3.18
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
                self._attrs[v] = kwargs.pop(k)
            elif v in kwargs:
                self._attrs[v] = kwargs.pop(v)  # from a duplicated instance
            else:
                logger.debug(f"OC Instance Args no longer exists in {self}: {k}")
                continue
            setattr(self, v, self._attrs[v])

        self.attrs = kwargs

        self.label = self._attrs.get("label", self.entdata.get("@Name", Path(self.name).stem))
        self.position = vector_from_csv(self._attrs.get("position", "0,0,0"))

        try:
            self.hidden = self._sc.datacore.records_by_guid[self.attrs['starMapRecord']].properties.hideInStarmap
        except (KeyError, AttributeError):
            self.hidden = False
        try:
            self.size = self._sc.datacore.records_by_guid[self.attrs['starMapRecord']].properties.size
        except (KeyError, AttributeError):
            self.size = 0
        try:
            self.icon = self._sc.datacore.records_by_guid[self.attrs['starMapRecord']].properties.navIcon
        except (KeyError, AttributeError):
            self.icon = None
        try:
            self.geom = self._sc.datacore.records_by_guid[self.attrs['starMapRecord']].properties.starMapGeomPath
        except (KeyError, AttributeError):
            self.geom = None
        try:
            self.mtl = self._sc.datacore.records_by_guid[self.attrs['starMapRecord']].properties.starMapMaterialPath
        except (KeyError, AttributeError):
            self.mtl = None

        self.rotation = quaternion_from_csv(self._attrs.get("rotation", "1,0,0,0"))
        if "entity_name" not in self._attrs:
            self._attrs["entity_name"] = ""
            self.entity_name = ""

        self.display_name = self.entity_name
        if 'starMapRecord' in self.attrs:
            try:
                self.display_name = self._sc.gettext(
                    self._sc.datacore.records_by_guid[self.attrs['starMapRecord']].name
                )
            except KeyError:
                pass

        if isinstance(parent, ObjectContainerInstance):
            self.universal_position = parent.universal_position + self.position
            self.universal_rotation = (
                    parent.universal_rotation * self.rotation
            )  # TODO: is this actually a thing?
        else:
            self.universal_position = self.position
            self.universal_rotation = self.rotation

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
                "hidden": self.hidden,
                "universal_positional": self.universal_position,
                "universal_rotation": self.universal_rotation,
                "position": self.position,
                "rotation": self.rotation,
                "geom": self.geom,
                "mtl": self.mtl,
                "size": self.size,
                "icon": self.icon,
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


try:
    from pyvistaqt import QtInteractor
    import pyvista as pv
    from pyvista import examples

    cubemap = examples.download_cubemap_space_16k()

    class ObjectContainerPlotter(QtInteractor):
        def __init__(self, object_container, depth_to_show=1, label_font_size=48, point_max_size=48,
                     *args, **kwargs):
            self.object_container = object_container
            self.depth_to_show = depth_to_show
            self.label_font_size = label_font_size
            self.point_max_size = point_max_size

            super().__init__(*args, **kwargs)

            # self.plotter = plotter or Plotter(*args, **kwargs)
            # self.plotter.add_key_event('r', self._handle_reset_view)
            # self.plotter.enable_fly_to_right_click()
            # self.plotter.enable_point_picking(self._handle_clicked_point, show_message=False, left_clicking=True)


            self.add_key_event('r', self._handle_reset_view)
            self.enable_fly_to_right_click()
            self.enable_point_picking(self._handle_clicked_point, show_message=False, left_clicking=True)

            self._oc_from_point = {}
            self._parent_oc = [self.object_container]

            self._update_plotter()

        def _handle_reset_view(self):
            self.reset_camera()

        def _handle_clicked_point(self, point):
            self.fly_to(point)
            if oc := self._oc_from_point.get(tuple(point)):
                self._update_plotter(oc)

        def _handle_button_clicked(self, _):
            if not self._parent_oc:
                self._parent_oc = [self.object_container]
            else:
                self._parent_oc.pop()
            self._update_plotter(self._parent_oc[-1])

        def _update_plotter(self, base_oc=None):
            base_oc = base_oc or self.object_container

            self.clear()
            self._oc_from_point.clear()
            self.add_actor(cubemap.to_skybox())
            self.set_environment_texture(cubemap, True)

            if base_oc is not self.object_container:
                if base_oc not in self._parent_oc:
                    self._parent_oc.append(base_oc)
                self.add_checkbox_button_widget(self._handle_button_clicked, value=True)

            points_at_size = {}

            def add_children(obj, d, s):
                if d < 0:
                    return
                name = getattr(obj, 'display_name', obj.name)
                point = tuple(getattr(obj, 'universal_position', (0, 0, 0)))
                self._oc_from_point[point] = obj
                points_at_size.setdefault(s, []).append((point, name))
                for child in obj.children.values():
                    add_children(child, d - 1, max(5, s - 10))

            self.add_text(getattr(base_oc, 'display_name', base_oc.name))
            add_children(base_oc, d=max(self.depth_to_show, 1), s=self.point_max_size)

            for size in points_at_size:
                points, names = zip(*points_at_size[size])
                self.add_point_labels(points, names, font_size=self.label_font_size, pickable=True,
                                      reset_camera=True, point_size=size, shape='rounded_rect')
            # self.show_bounds()
            # plotter.enable_joystick_style()
            # self.show_grid()

except ImportError:
    class ObjectContainerPlotter:
        def __init__(self):
            from pyvista import Plotter  # trigger import error
