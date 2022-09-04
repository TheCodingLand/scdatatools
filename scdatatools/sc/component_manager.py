import logging
from typing import TYPE_CHECKING

from scdatatools.forge.dco import dco_from_guid
from scdatatools.forge.dco.entities import Entity

if TYPE_CHECKING:
    from scdatatools.sc import StarCitizen

logger = logging.getLogger(__name__)


class AttachableComponentManager:
    def __init__(self, sc: "StarCitizen"):
        self.sc = sc
        self.attachable_components = {}

        self.by_type = {}
        self.by_sub_type = {}
        self.by_size = {}
        self.by_tag = {}

    def load_attachable_components(self):
        for record in self.sc.datacore.entities.values():
            try:
                entity: Entity = dco_from_guid(self.sc, record)
                if 'SAttachableComponentParams' not in entity.components:
                    continue
                self.attachable_components[entity.name] = entity
                ac = entity.components['SAttachableComponentParams']
                self.by_size.setdefault(ac.size, []).append(entity)
                self.by_type.setdefault(ac.attachable_type, []).append(entity)
                self.by_sub_type.setdefault(ac.attachable_sub_type, []).append(entity)
                for tag in ac.tags:
                    self.by_tag.setdefault(tag, []).append(entity)
            except Exception as e:
                logger.exception(f'Failed to process attachable component {record.filename}', exc_info=e)

    def filter(self, name=None, size=None, type=None, sub_type=None, tags=None):
        entities = self.attachable_components.values()
        if name is not None:
            entities = [_ for _ in entities if name in _]
        if size is not None:
            entities = [_ for _ in self.by_size.get(size, []) if _ in entities]
        if type is not None:
            entities = [_ for _ in self.by_type.get(type, []) if _ in entities]
        if sub_type is not None:
            entities = [_ for _ in self.by_sub_type.get(sub_type, []) if _ in entities]
        if tags is not None:
            for tag in tags:
                entities = [_ for _ in self.by_tag.get(tag, []) if _ in entities]
        return entities
