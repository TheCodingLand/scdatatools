import typing
from pathlib import Path

from nubia import command, argument

from scdatatools.sc.textures import convert_dds, unsplit_dds


@command(help="Recombine split DDS texture files (dds.N). This will attempt to locate the DDS pieces.")
@argument("dds_files", description="DDS file to recombine. Split pieces will be found automatically.", positional=True)
@argument("replace", description="Output a single DDS file and also remove the pieces", aliases=["-r"])
def dds_unsplit(dds_files: typing.List[str], replace: bool = False):
    found_files = {}
    for ddsfile in dds_files:
        ddsfile = Path(ddsfile)
        dds_basename = ddsfile.parent / ddsfile.name.split('.')[0]
        found_files.setdefault(str(dds_basename.absolute()),
                               set()).update(dds_basename.parent.glob(f'{dds_basename.name}.dds*'))

    for ddsfile in found_files:
        try:
            if len(found_files[ddsfile]) == 1:
                continue

            print(f'{ddsfile} -> ', end='')
            d = unsplit_dds({_.name: _.open('rb').read() for _ in found_files[ddsfile]})
            if replace:
                outfile = Path(f'{ddsfile}.dds')
                [_.unlink(missing_ok=True) for _ in found_files[ddsfile]]
            else:
                outfile = Path(f'{ddsfile}_full.dds')

            with outfile.open('wb') as out:
                out.write(d)
                print(f'{outfile}')
        except Exception as e:
            print(f'failed: {repr(e)}')
