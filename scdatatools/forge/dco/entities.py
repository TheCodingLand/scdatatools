from .common import DataCoreObject, register_record_handler, dco_from_guid


@register_record_handler("EntityClassDefinition")
class Entity(DataCoreObject):
    def __init__(self, datacore, guid):
        super().__init__(datacore, guid)

        self.components = {}
        for c in sorted(self.record.properties["Components"], key=lambda c: c.name):
            if c.name in self.components:
                print(f"WARNING: Duplicate component for entity, shouldnt be possible? {c.name}")
                continue
            self.components[c.name] = c
        self.tags = [
            dco_from_guid(self._datacore, t.name) for t in self.record.properties["tags"] if t.name
        ]


@register_record_handler(
    "EntityClassDefinition",
    filename_match="libs/foundry/records/entities/spaceships/.*",
)
class Ship(Entity):
    @property
    def category(self):
        return self.record.properties["Category"]

    @property
    def icon(self):
        return self.record.properties["Icon"]

    @property
    def invisible(self):
        return self.record.properties["Invisible"]

    @property
    def bbox_selection(self):
        return self.record.properties["BBoxSelection"]

    @property
    def lifetime_policy(self):
        return dco_from_guid(self._datacore, self.record.properties["lifetimePolicy"])

    @property
    def object_containers(self):
        return self.components["VehicleComponentParams"].properties["objectContainers"]

    def __repr__(self):
        return f"<DCO Ship {self.name}>"
