import typing
from pathlib import Path

from nubia import command, argument

from scdatatools.sc.textures import tex_convert, unsplit_dds
from scdatatools.sc.textures.dds import is_glossmap


@command
class Tex:
    """ Texture processing commands """
    @command(help="Recombine split DDS texture files (dds.N). This will attempt to locate the DDS pieces.")
    @argument("dds_files", description="DDS file to recombine. Split pieces will be found automatically.",
              positional=True)
    @argument(
        "outdir", aliases=["-o"],
        description="Output directory to place unsplit textures. By default, the output texture will be placed next to"
                    "the input texture with '_full' appended to it's filename, or be replaced if -r is specified",
    )
    @argument("replace", aliases=["-r"],
              description="Replace the DDS file and also remove the pieces. Only if not output directory is specified")
    def dds_unsplit(self, dds_files: typing.List[str], outdir: str = '', replace: bool = False):
        found_files = {}
        for ddsfile in dds_files:
            ddsfile = Path(ddsfile).absolute()
            dds_basename = ddsfile.name.split('.')[0] + ('.dds.a' if is_glossmap(ddsfile) else '.dds')
            dds_basename = ddsfile.parent / dds_basename
            if is_glossmap(ddsfile):
                found_files.setdefault(dds_basename.absolute(), {dds_basename.absolute()}).update(
                    _ for _ in dds_basename.parent.glob(f'{dds_basename.stem}.[0-9]a') if is_glossmap(_)
                )
            else:
                found_files.setdefault(dds_basename.absolute(), {dds_basename.absolute()}).update(
                    _ for _ in dds_basename.parent.glob(f'{dds_basename.stem}.dds.[0-9]') if not is_glossmap(_)
                )

        for ddsfile in found_files:
            try:
                if len(found_files[ddsfile]) == 1:
                    continue

                print(f'{ddsfile} -> ', end='')
                d = unsplit_dds({_.name: _ for _ in found_files[ddsfile]})
                if outdir:
                    outfile = Path(outdir).absolute() / f'{Path(ddsfile).name}'
                elif replace:
                    outfile = Path(f'{ddsfile}')
                    [_.unlink(missing_ok=True) for _ in found_files[ddsfile]]
                else:
                    stem, ext = str(ddsfile.name).split('.', maxsplit=1)
                    outfile = Path(f'{stem}_full.{ext}')

                print(outfile)
                with outfile.open('wb') as out:
                    out.write(d)
            except Exception as e:
                raise
                print(f'failed: {repr(e)}')
