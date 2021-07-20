
from .common import DataCoreObject, register_record_handler


@register_record_handler('Tag')
class Tag(DataCoreObject):
    def __init__(self, datacore, tag_guid):
        super().__init__(datacore, tag_guid)
        assert self.record.type == 'Tag'

    @property
    def name(self):
        return self.record.properties['tagName']

    @property
    def legacy_guid(self):
        return self.record.properties['legacyGUID']

    @property
    def children(self):
        return [Tag(self._datacore, t.name) for t in self.record.properties['children']]
