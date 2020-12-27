import io
import json
from pathlib import Path

from tqdm import tqdm
from nubia import command, argument

from scdatatools.sc import StarCitizen


@command(help="Dumps a JSON object of every file in the Star Citizen directory and archives (recursively). This is"
              "used to compare different versions of Star Citizen")
@argument("scdir", description="StarCitizen Game Folder")
@argument("outfile", description="Output file name")
def inventory(
        scdir: Path,
        outfile: Path
):
    sc = StarCitizen(scdir)
    i = sc.generate_inventory()

    with tqdm.wrapattr(outfile.open('w'), 'write', desc=f'Writing {outfile.name}') as o:
        # default=str will handle the datetimes
        json.dump(i, o, indent=2, default=str)
