import sys
import shutil
import typing
import tempfile
from pathlib import Path

from nubia import command, argument

from scdatatools import forge
from scdatatools import p4k


def _dump_record(dcb, record, output, xml=False):
    if output == '-':
        if xml:
            sys.stdout.write(dcb.dump_record_xml(record))
        else:
            sys.stdout.write(dcb.dump_record_json(record))
    else:
        if not output.suffix:
            output = output / Path(record.filename)
        output.parent.mkdir(parents=True, exist_ok=True)
        print(str(output))
        try:
            with open(str(output), "w") as target:
                if xml:
                    target.writelines(dcb.dump_record_xml(record))
                else:
                    target.writelines(dcb.dump_record_json(record))
        except ValueError as e:
            print(f"ERROR: Error processing {record.filename}: {e}")


@command(help="Convert a DataForge file to a readable format", exclusive_arguments=('xml', 'json'))
@argument("forge_file", description="DataForge (.dcb) file to extract data from. (or Data.p4k)", positional=True)
@argument("single", description="Extract first matching file only", aliases=["-1"])
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
    aliases=["-f"]
)
def unforge(
        forge_file: typing.Text,
        file_filter: typing.Text = "*",
        output: typing.Text = ".",
        xml: bool = True,
        json: bool = False,
        single: bool = False,
):
    forge_file = Path(forge_file)
    output = Path(output).absolute() if output != '-' else output
    file_filter = file_filter.strip("'").strip('"')

    if not forge_file.is_file():
        sys.stderr.write(f"Could not open DataForge file from {forge_file}\n")
        sys.exit(1)

    if forge_file.suffix == '.p4k':
        print(f'Opening {forge_file}')
        p = p4k.P4KFile(forge_file)
        dcb = p.search('*Game.dcb')
        if len(dcb) != 1:
            raise ValueError('Could not determine the location of the datacore')
        with p.open(dcb[0]) as f:
            print(f"Opening DataForge file from: {forge_file}")
            _datacore_tmp = tempfile.TemporaryFile()
            shutil.copyfileobj(f, _datacore_tmp)
            _datacore_tmp.seek(0)
            dcb = forge.DataCoreBinary(forge.DataCoreBinaryMMap(_datacore_tmp))
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

        print(f"Extracting {record.filename}")
        _dump_record(dcb, record, output, not json)
    else:
        print(f"Extracting files into {output} with filter '{file_filter}'")
        print("=" * 120)
        for record in dcb.search_filename(file_filter):
            _dump_record(dcb, record, output, not json)
