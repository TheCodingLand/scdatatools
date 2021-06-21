from .common import DataCoreObject, register_record_handler, dco_from_guid


@register_record_handler('EntityClassDefinition')
class Entity(DataCoreObject):
    def __init__(self, sc, guid):
        super().__init__(sc, guid)

        self.components = {}
        for c in self.record.properties['Components']:
            if c.name in self.components:
                print(f'WARNING: Duplicate component for entity, shouldnt be possible? {c.name}')
                continue
            self.components[c.name] = c
        self.tags = [dco_from_guid(self._sc, t.name) for t in self.record.properties['tags']]


@register_record_handler('EntityClassDefinition', filename_match='libs/foundry/records/entities/spaceships/.*')
class Ship(Entity):
    @property
    def category(self):
        return self.record.properties['Category']

    @property
    def icon(self):
        return self.record.properties['Icon']

    @property
    def invisible(self):
        return self.record.properties['Invisible']

    @property
    def bbox_selection(self):
        return self.record.properties['BBoxSelection']

    @property
    def lifetime_policy(self):
        return dco_from_guid(self._sc, self.record.properties['lifetimePolicy'])

    @property
    def object_containers(self):
        return self.components['VehicleComponentParams'].properties['objectContainers']
