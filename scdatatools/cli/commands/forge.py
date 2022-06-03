import sys
import typing
from pathlib import Path
import concurrent.futures

from tqdm import tqdm
from nubia import command, argument

from scdatatools import forge
from scdatatools.sc import StarCitizen


def _dump_record(dcb, record, output, guid, guid_if_exists, xml, quiet):
    if output == "-":
        if xml:
            sys.stdout.write(dcb.dump_record_xml(record))
        else:
            sys.stdout.write(dcb.dump_record_json(record))
    else:
        if output.is_dir():
            output = output / Path(record.filename)
        output.parent.mkdir(parents=True, exist_ok=True)
        suffix = ".xml" if xml else ".json"
        if guid or (guid_if_exists and output.is_file()):
            output = output.parent / f"{output.stem}.{record.id.value}{suffix}"
        else:
            output = output.parent / f"{output.stem}{suffix}"
        if not quiet:
            print(str(output))
        try:
            with open(str(output), "w") as target:
                if xml:
                    target.writelines(dcb.dump_record_xml(record))
                else:
                    target.writelines(dcb.dump_record_json(record))
        except ValueError as e:
            print(f"ERROR: Error processing {record.filename}: {e}")


@command(
    help="Convert a DataForge file to a readable format",
    exclusive_arguments=("xml", "json"),
)
@argument(
    "forge_file",
    description="DataForge (.dcb) file to extract data from. (or Data.p4k)",
    positional=True,
)
@argument("single", description="Extract first matching file only", aliases=["-1"])
@argument(
    "guid",
    aliases=["-g"],
    description="Include the GUID in the filename (avoids overwriting from records with the same 'filename') "
    "(Default: False)",
)
@argument(
    "guid_if_exists",
    aliases=["-G"],
    description="Include the GUID in the filename only if the output file already exists. (Default: True)",
)
@argument("xml", aliases=["-x"], description="Convert to XML (Default)")
@argument("json", aliases=["-j"], description="Convert to JSON")
@argument(
    "output",
    description="The output directory to extract files into or the output path if --single. "
    "Defaults to current directory. Use '-' to output a single file to the stdout",
    aliases=["-o"],
)
@argument(
    "file_filter",
    description="Posix style file filter of which files to extract",
    aliases=["-f"],
)
def unforge(
    forge_file: typing.Text,
    file_filter: typing.Text = "*",
    output: typing.Text = ".",
    guid: bool = False,
    guid_if_exists: bool = True,
    xml: bool = True,
    json: bool = False,
    single: bool = False,
    quiet: bool = True,
):
    """ Extracts DataCore records and converts them to a given format (xml/json). Use the `--file-filter` argument to
    down-select which records to extract, by default it will extract all of them to the `--output` directory."""
    forge_file = Path(forge_file)
    output = Path(output).absolute() if output != "-" else output
    file_filter = file_filter.strip("'").strip('"')

    if not forge_file.is_file():
        sys.stderr.write(f"Could not open DataForge file from {forge_file}\n")
        sys.exit(1)

    if forge_file.suffix.casefold() == ".p4k":
        print(f"Opening DataCore from {forge_file}")
        sc = StarCitizen(forge_file)
        dcb = sc.datacore
    else:
        print(f"Opening DataForge file: {forge_file}")
        dcb = forge.DataCoreBinary(str(forge_file))

    if single:
        print(f"Extracting first match for filter '{file_filter}' to {output}")
        print("=" * 120)
        records = dcb.search_filename(file_filter)
        if not records:
            sys.stderr.write(f"No files found for filter")
            sys.exit(2)
        record = records[0]

        _dump_record(dcb, record, output, guid, guid_if_exists, not json, quiet=quiet)
    else:
        print(f"Extracting files into {output} with filter '{file_filter}'")
        print("=" * 120)
        try:
            for record in tqdm(dcb.search_filename(file_filter), desc="Extracting records", unit_scale=True, unit='r'):
                _dump_record(dcb, record, output, guid, guid_if_exists, not json, quiet=quiet)
        except KeyboardInterrupt:
            pass
