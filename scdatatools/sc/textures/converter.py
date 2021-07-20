import io
import os
import enum
import shutil
import sys
import typing
import tempfile
import subprocess
from pathlib import Path

from .dds import is_glossmap, is_normals
from scdatatools.utils import NamedBytesIO


class ConverterUtility(enum.Enum):
    default = 'default'
    texconv = 'texconv'
    compressonator = 'compressonator'


# normal maps and glossmaps need to force texconv to use specific formats
TEXCONV_DEFAULT_FMT = 'rgba'
TEXCONV_DDNA_FMT = 'B8G8R8A8_UNORM'
TEXCONV_GLOSSMAP_FMT = 'R8G8B8A8_UNORM'

DEFAULT_TEXCONV_ARGS = '-f {fmt} -nologo'
DEFAULT_COMPRESSONATOR_ARGS = '-noprogress'

DDS_CONV_FALLBACK = 'png'
DDS_CONV_FORMAT = {
    'linux': 'png',
    'darwin': 'png',
    'win32': 'png'  # this used to be tif, then i asked myself, why?
}


class ConverterUnavailable(Exception):
    pass


class ConversionError(Exception):
    pass


COMPRESSONATORCLI = shutil.which('compressonatorcli')
TEXCONV = shutil.which('texconv')


def _check_bin(converter=ConverterUtility.default, converter_bin=''):
    if converter != ConverterUtility.default and converter_bin:
        return converter, converter_bin
    elif converter == ConverterUtility.default and converter_bin:
        raise ValueError(f'Cannot specify converter_bin and `ConverterUtility.default` at the same time')

    if converter == ConverterUtility.default:
        if TEXCONV:
            return ConverterUtility.texconv, TEXCONV
        if COMPRESSONATORCLI:
            return ConverterUtility.compressonator, COMPRESSONATORCLI
        raise ConverterUnavailable('Converter is not available. Please make sure `texconv` or '
                                   '`compressonatorcli` is in your system PATH')
    elif converter == ConverterUtility.texconv:
        if not TEXCONV:
            raise ConverterUnavailable('Converter is not available. Please make sure `texconv` is in your system PATH')
        return ConverterUtility.texconv, TEXCONV
    elif converter == ConverterUtility.compressonator:
        if not COMPRESSONATORCLI:
            raise ConverterUnavailable('Converter is not available. Please make sure `compressonatorcli` is in your '
                                       'system PATH')
        return ConverterUtility.compressonator, COMPRESSONATORCLI
    else:
        raise ValueError(f'Invalid ConverterUtility: {converter}')


def convert_buffer(inbuf: bytes, in_format: object, out_format: str = 'default',
                   converter: ConverterUtility = ConverterUtility.default, converter_cli_args: str = "",
                   converter_bin: str = '') -> (bytes, str):
    """
    Converts a buffer `inbuf` to the output format `out_format`. See :func:convert for more information on parameters

    :param inbuf: Bytes of the texture to convert
    :param in_format: `str` of the import format (e.g. 'dds')
    :param out_format: The desired output format. Default's to the default output format for the platform, 'default'
    :param converter: Which :enum:`ConverterUtility` to use for the conversion (texconv or compressonatorcli)
    :param converter_cli_args: Additional command line args to pass to the converter.`converter` cannot be `default`
    :param converter_bin: Override the path to the converter binary. `converter` cannot be `default`
    :return: Tuple containing the `bytes` of the converted image in the specified `output_format` and the `str` format
        of the returned `bytes`
    """

    out_format = DDS_CONV_FORMAT.get(sys.platform, DDS_CONV_FALLBACK) if out_format == 'default' else out_format
    out_format = out_format.replace('.', '')
    _ = tempfile.NamedTemporaryFile(suffix=f'.{out_format}')
    tmpout = Path(_.name)
    _.close()

    tex_convert(NamedBytesIO(inbuf, name=f'tmp.{in_format}'), tmpout, converter=converter,
                converter_cli_args=converter_cli_args, converter_bin=converter_bin)

    tex = tmpout.open('rb').read()
    Path(tmpout).unlink()

    return tex, out_format


def tex_convert(infile: typing.Union[str, Path, io.BufferedIOBase, io.RawIOBase, NamedBytesIO],
                outfile: typing.Union[str, Path], converter: ConverterUtility = ConverterUtility.default,
                converter_cli_args: str = "", converter_bin: str = "") -> bytes:
    """ Convert the texture file provided by `infile` to `outfile` using the an external converter. By default, this
    will attempt to use `texconv`. If that fails, or isn't available, then it'll attempt to use `compressonatorcli`.
    Setting `converter` explicitly will disable this behavior and only attempt the chosen `converter`.

    :param infile:  A `str`, `Path`, file-like object or bytes of the input texture
    :param outfile:  The output file path. Default of '-' will return the buffer
    :param converter:  Which converter to use. By default `texconv` will be used if available, if not then
        `compressonatorcli`. Set to `converter.COMPRESSONATOR` force using `compressonatorcli`.
    :param converter_cli_args:  Override the additional CLI arguments passed to the converter. You must specify which
        converter to use when specifying `cli_args`
    :param converter_bin: Override the path to the converter binary. `converter` cannot be `default`
    :raises:
        ConversionError: If the converter does not exit cleanly. Output from the converter will be supplied
        ConverterUnavailable: If the specified `converter_bin` is invalid, or if `texconv` or `compressonatorcli`
            cannot be found on the system's `PATH`
    :return: `bytes` of the converted image in format determined by `outfile`'s extension
    """
    if converter_cli_args and converter == ConverterUtility.default:
        raise ValueError(f'You must specify which converter to use when supplying converter_cli_args')
    try_compressonator = converter == ConverterUtility.default
    converter, converter_bin = _check_bin(converter, converter_bin)

    if is_glossmap(infile):
        raise NotImplementedError(f'Cannot yet convert glossmaps. Please check for an option issue with scdatatools'
                                  f'if you require this functionality.')

    if isinstance(outfile, str):
        outfile = Path(outfile)
    if outfile.exists():
        raise ValueError(f'outfile "{outfile}" already exists')

    _delete = True
    if isinstance(infile, (str, Path)):
        tmpin = open(infile, 'rb')
        _delete = False
    else:
        infile.seek(0)
        tmpin = tempfile.NamedTemporaryFile(suffix=Path(infile.name).suffix, delete=False)
        tmpin.write(infile.read())

    r = None
    try:
        # TODO: logging...

        # Make sure we're not preventing access to the in file
        tmpin.close()
        ft = outfile.suffix[1:]  # remove the '.'

        # use `texconv`
        err_msg = ''
        if converter in [ConverterUtility.default, ConverterUtility.texconv]:
            if is_glossmap(infile.name):
                converter_cli_args = DEFAULT_TEXCONV_ARGS.format(fmt=TEXCONV_GLOSSMAP_FMT) + converter_cli_args
            elif is_normals(infile.name):
                converter_cli_args = DEFAULT_TEXCONV_ARGS.format(fmt=TEXCONV_DDNA_FMT) + converter_cli_args
            else:
                converter_cli_args = DEFAULT_TEXCONV_ARGS.format(fmt=TEXCONV_DEFAULT_FMT) + converter_cli_args
            cmd = f'{converter_bin} -ft {ft} {converter_cli_args} {tmpin.name}'
            try:
                r = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True,
                                   cwd=outfile.parent)
                # texconv outputs to the same location as the input file, so move it to the requested output path
                # if successful
                shutil.move(outfile.parent / f'{Path(tmpin.name).stem}.{ft}', outfile.absolute())
                return
            except subprocess.CalledProcessError as e:
                err_msg = f'Error converting with texconv: {e.output.decode("utf-8", errors="ignore")}'
                if not try_compressonator:
                    raise ConversionError(err_msg)

        # use `compressonatorcli` if chosen, or if texconv isn't available/failed and default is chosen
        if converter == ConverterUtility.compressonator or try_compressonator:
            if try_compressonator:
                # we've failed into compressonator, so pick-up it's path
                try:
                    converter, converter_bin = _check_bin(ConverterUtility.compressonator)
                except ConverterUnavailable:
                    # compressonator not available, return texconv error
                    raise ConversionError(err_msg)
            converter_cli_args = converter_cli_args if converter_cli_args else DEFAULT_COMPRESSONATOR_ARGS
            cmd = f'{converter_bin} {converter_cli_args} {tmpin.name} {outfile.absolute()}'
            try:
                r = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)
            except subprocess.CalledProcessError as e:
                err = f'Error converting with compressonator: {e.output.decode("utf-8", errors="ignore")}'
                if err_msg:
                    raise ConversionError(f'Failed to convert with texconv and compressonatorcli:\n\n{err_msg}\n{err}')
                raise ConversionError(err)
    finally:
        if _delete:
            Path(tmpin.name).unlink(missing_ok=True)
