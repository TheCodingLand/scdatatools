import sys
import shutil
import typing
import struct
import tempfile
import subprocess
from pathlib import Path

from scdatatools.p4k import P4KFile


DDS_CONV_FALLBACK = 'png'
DDS_CONV_FORMAT = {
    'linux': 'png',
    'darwin': 'png',
    'win32': 'tif'
}
COMPRESSONATORCLI = shutil.which('compressonatorcli')
TEXCONV = shutil.which('texconv')


def unsplit_dds(dds_files: typing.Dict[str, typing.Union[P4KFile, typing.IO, bytes]]) -> bytes:
    """
    Recombines split Star Citizen DDS texture files (`.dds.N` files).

    :param dds_files: `dict` containing all the pieces of a split texture file. The key should be the
        filename of the component, and the value should be a file-like object or bytes of the texture.
    :return: Recombined texture file as a `bytes` object
    """

    try:
        dds_header = dds_files.pop([_ for _ in dds_files if _.endswith('.dds')][0])
    except (IndexError, KeyError):
        raise ValueError(f'Could not determine the DDS header file from {",".join(dds_files.keys())}')

    if not isinstance(dds_header, bytes):
        dds_header = dds_header.read()

    dds_magic, dds_hdr_len = struct.unpack('<4sI', dds_header[:8])
    dds_hdr_len += 4  # does not include the magic bytes
    if dds_magic != b'DDS ':
        raise ValueError(f'Invalid DDS header')

    dds_file = dds_header[:dds_hdr_len]
    for dds in sorted([_ for _ in dds_files.keys()], reverse=True, key=lambda d: d.split('.')[-1]):
        if isinstance(dds_files[dds], bytes):
            dds_file += dds_files[dds]
        else:
            dds_file += dds_files[dds].read()
    dds_file += dds_header[dds_hdr_len:]

    return dds_file


def convert_dds(dds_file: typing.Union[bytes, typing.IO], texconv='', compressonatorcli='',
                output_format='default') -> (bytes, str):
    """
    Convert a DDS texture file to `output_format`, using `texconv` or `compressonatorcli`. `texconv` utility will be
    used if available, otherwise compressonatorcli. If texconv/compressonatorcli are empty, they will be looked up from
    the system's `PATH`.

    :param dds_file: DDS texture bytes or file to convert
    :param texconv: Path to `texconv.exe`. Will be auto-detected if available.
    :param compressonatorcli: Path to `compressonatorcli`. Will be auto-detected if available.
    :param output_format: The format to convert to. The default of `default` will convert to `tif` on Windows, and
        `png` for all other platforms.
    :return: Tuple containing the `bytes` of the converted image in the specified `output_format` and the `str` format
        of the returned `bytes`
    """
    texconv = texconv or TEXCONV
    compressonatorcli = compressonatorcli or COMPRESSONATORCLI

    if not texconv and not compressonatorcli:
        raise RuntimeError(f'Could not find/determine texture converter utility')

    if output_format == 'default':
        output_format = DDS_CONV_FORMAT.get(sys.platform, DDS_CONV_FALLBACK)

    tmpin = tempfile.NamedTemporaryFile(delete=False, suffix='.dds')
    tmpin.write(dds_file if isinstance(dds_file, bytes) else dds_file.read())
    tmpin.close()
    tmpout = Path(tmpin.name.replace('.dds', f'.{output_format}'))

    texconv_err = ''
    if texconv:
        cmd = f'{texconv} -ft {output_format} -f rgba -nologo {tmpin.name}'
        try:
            subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True,
                           cwd=tmpout.parent)
        except subprocess.CalledProcessError as e:
            texconv_err = e.output.decode('utf-8')
    if not texconv or texconv_err:
        cmd = f'{compressonatorcli} -noprogress {tmpin.name} {tmpout.absolute()}'
        try:
            subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)
        except subprocess.CalledProcessError as e:
            if texconv_err:
                raise RuntimeError(f'Error converting with texconv: {texconv_err}')
            raise RuntimeError(f'Error converting with compressonator: {repr(e)}')

    data = tmpout.open('rb').read()
    Path(tmpin.name).unlink(missing_ok=True)
    tmpout.unlink(missing_ok=True)
    return data, output_format
