import sys
import shutil
import typing
from pathlib import Path

from nubia import command, argument

from scdatatools import p4k


@command(help="Extract files from a P4K file")
@argument("p4k_file", description="P4K file to unpack files from", positional=True)
@argument("single", description="Extract first matching file only", aliases=["-1"])
@argument(
    "convert_cryxml",
    description="Automatically convert CryXmlB files to specified format.",
    choices=["xml", "json"],
    aliases=["-c"],
)
@argument(
    "extract_model_assets",
    description="Automatically select and extract assets (materials and textures) for model files that are being "
                "extracted, in addition to the search filter",
    aliases=["-A"]
)
@argument(
    "unsplit_textures",
    description="Automatically recombine split .dds texture files",
    aliases=["-T"]
)
@argument(
    "convert_textures",
    description="Convert textures to the given image format. This also enables unsplit_textures.",
    choices=["png", "tif", "tga"]
)
@argument(
    "convert_models",
    description="Automatically convert 3d models to COLLADA.",
    aliases=["-m"]
)
@argument(
    "no_overwrite",
    description="Do not overwrite existing files.",
    aliases=["-O"]
)
@argument(
    "output",
    description="The output directory to extract files into or the output path if --single. "
    "Defaults to current directory",
    aliases=["-o"],
)
@argument(
    "file_filter",
    description="Posix style file filter of which files to extract. Defaults to '*'",
    aliases=["-f"],
)
@argument("quiet", description="Don't output progress.", aliases=["-q"])
def unp4k(
    p4k_file: typing.Text,
    output: typing.Text = ".",
    file_filter: typing.Text = "*",
    convert_cryxml: typing.Text = "",
    extract_model_assets: bool = False,
    unsplit_textures: bool = False,
    convert_textures: str = "",
    convert_models: bool = False,
    single: bool = False,
    no_overwrite: bool = False,
    quiet: bool = False,
):
    output = Path(output).absolute()
    p4k_file = Path(p4k_file)
    file_filter = file_filter.strip("'").strip('"')

    if not p4k_file.is_file():
        sys.stderr.write(f"Could not open p4k file {p4k_file}\n")
        sys.exit(1)

    print(f"Opening p4k file: {p4k_file}")
    try:
        p = p4k.P4KFile(str(p4k_file))
    except KeyboardInterrupt:
        sys.exit(1)

    unsplit_textures = unsplit_textures or convert_textures != ""

    converters = []
    converter_options = dict()
    if convert_cryxml:
        converter_options.update({"cryxml_converter_fmt": convert_cryxml})
        converters.append("cryxml_converter")
    if unsplit_textures:
        converters.append('ddstexture_converter')
        converter_options.update({
            "ddstexture_converter_unsplit": True,
            "ddstexture_converter_replace": not no_overwrite,
        })
        if convert_textures:
            converter_options["ddstexture_converter_fmt"] = convert_textures
        else:
            converter_options["ddstexture_converter_fmt"] = "dds"
    if convert_models:
        converters.append("cgf_converter")
        # convert spaces in material names for dae conversion
        converter_options["cryxml_converter_mtl_fix_names"] = True

    if single:
        print(f"Extracting first match for filter '{file_filter}' to {output}")
        print("=" * 80)
        found_files = p.search(file_filter)
        if not found_files:
            sys.stderr.write(f"No files found for filter")
            sys.exit(2)
        extract_file = found_files[0]

        print(f"Extracting {extract_file.filename}")

        if output.name:
            # given an output name - use it instead of the name in the P4K
            output.parent.mkdir(parents=True, exist_ok=True)
            with p.open(extract_file) as source, open(str(output), "wb") as target:
                shutil.copyfileobj(source, target)
        else:
            output.mkdir(parents=True, exist_ok=True)
            p.extract(extract_file, path=str(output), converters=converters)

    else:
        print(f"Extracting files into {output} with filter '{file_filter}'")
        print("=" * 80)
        output.mkdir(parents=True, exist_ok=True)
        try:
            p.extract_filter(
                file_filter=file_filter,
                path=str(output),
                converters=converters,
                converter_options=converter_options,
                overwrite=not no_overwrite,
            )
        except KeyboardInterrupt:
            pass
