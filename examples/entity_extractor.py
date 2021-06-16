from datetime import datetime
from scdatatools.sc import StarCitizen
from scdatatools.forge.dftypes import StructureInstance, ClassReference, GUID


def flatten_props(prop, name='', parent=''):
    d = {}
    if isinstance(prop, list):
        d.update(flatten_props({f'{i}': getattr(_, 'properties', _) for i, _ in enumerate(prop)},
                               parent=f'{parent}_{name}' if parent else (name or parent)))
    elif isinstance(prop, dict):
        for k, v in prop.items():
            d.update(flatten_props(v, name=k, parent=f'{parent}_{name}' if parent and name else (name or parent)))
    elif isinstance(prop, (StructureInstance, ClassReference)):
        d.update(flatten_props(prop.properties, name, parent))
    else:
        if isinstance(prop, GUID):
            prop = prop.value
        d[f'{parent}_{name}' if parent else name] = prop
    return d


def flatten_comps(name, obj, comps_to_flatten=None):
    comps_to_flatten = comps_to_flatten or []
    ctrl = {'name': name}
    has_params = False
    for comp in obj.properties['Components']:
        if comp.name in comps_to_flatten:
            has_params = True
            ctrl.update(flatten_props(comp.properties))
    return ctrl, has_params


def extract(sc_dir):
    sc = StarCitizen(sc_dir)
    print(f'Opening {sc.version_label}...')
    print(f'{len(sc.p4k.filelist)} files, {len(sc.datacore.records)} records')

    print(f'Starting extraction')
    start = datetime.now()
    ifcs = []
    ifcs_params = ['IFCSParams']
    # for name, obj in sc.datacore.records_by_path['libs/foundry/records/entities/scitem/ships/controller'].items():
    for obj in sc.datacore.search_filename('libs/foundry/records/entities/scitem/ships/controller/*'):
        c, has_params = flatten_comps(obj.name, obj, ifcs_params)
        if has_params:
            ifcs.append(c)
    print(f'{len(ifcs)} IFCS controllers processed')

    weapon_gun = []
    weapon_comps = ['SAttachableComponentParams', 'SAmmoContainerComponentParams', 'SDegradationParams',
                    'SHealthComponentParams', 'EntityComponentHeatConnection', 'EntityComponentPowerConnection']
    # for name, obj in sc.datacore.records_by_path['libs/foundry/records/entities/scitem/ships/weapons'].items():
    for obj in sc.datacore.search_filename('libs/foundry/records/entities/scitem/ships/weapons/*'):
        if isinstance(obj, dict):
            continue  # being lazy and skipping "sub-folders"
        c, has_params = flatten_comps(obj.name, obj, weapon_comps)
        if has_params:
            weapon_gun.append(c)
    print(f'{len(weapon_gun)} Ship weapons processed')

    armor = []
    armor_params = ['SCItemVehicleArmorParams']
    # for name, obj in sc.datacore.records_by_path['libs/foundry/records/entities/scitem/ships/armor'].items():
    for obj in sc.datacore.search_filename('libs/foundry/records/entities/scitem/ships/armor/*'):
        c, has_params = flatten_comps(obj.name, obj, armor_params)
        if has_params:
            armor.append(c)
    print(f'{len(armor)} Ship armors processed')

    qd = []
    qd_params = ['SCItemQuantumDriveParams', 'SHealthComponentParams', 'EntityComponentHeatConnection']
    # for name, obj in sc.datacore.records_by_path['libs/foundry/records/entities/scitem/ships/quantumdrive'].items():
    for obj in sc.datacore.search_filename('libs/foundry/records/entities/scitem/ships/quantumdrive/*'):
        c, has_params = flatten_comps(obj.name, obj, qd_params)
        if has_params:
            qd.append(c)
    print(f'{len(qd)} Ship qds processed')

    countermeasures = []
    countermeasures_params = ['SAttachableComponentParams', 'SCItemWeaponComponentParams']
    # for name, obj in sc.datacore.records_by_path['libs/foundry/records/entities/scitem/ships/countermeasures'].items():
    for obj in sc.datacore.search_filename('libs/foundry/records/entities/scitem/ships/countermeasures/*'):
        c, has_params = flatten_comps(obj.name, obj, countermeasures_params)
        if has_params:
            countermeasures.append(c)
    print(f'{len(countermeasures)} Ship countermeasures processed')
    print(f'Finished Extraction in {datetime.now() - start}')


if __name__ == '__main__':
    start = datetime.now()
    extract('d:/Games/RSI/StarCitizen/LIVE')
    print(f'Finished in {datetime.now() - start}')
