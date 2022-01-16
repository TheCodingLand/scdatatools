import time
import typing
import logging
import traceback
import subprocess
from pathlib import Path
import concurrent.futures

from scdatatools.utils import log_time
from scdatatools.engine.chunkfile.converter import CGF_CONVERTER_MODEL_EXTS, CGF_CONVERTER_TIMEOUT, \
    CGF_CONVERTER_DEFAULT_OPTS, CGF_CONVERTER
from scdatatools.engine.textures import tex_convert, collect_and_unsplit, is_glossmap, ConverterUtility
from scdatatools.engine.chunkfile.converter import CGFModelConverter
from scdatatools.engine.textures.converter import DDSTextureConverter
from scdatatools.engine.cryxml import CryXmlConversionFormat, CryXmlConverter

if typing.TYPE_CHECKING:
    from .base import Blueprint

logger = logging.getLogger(__name__)
RECORDS_BASE_PATH = Path('libs/foundry/records/')


def _bp_monitor(msg, progress=None, total=None, level=logging.INFO, exc_info=None):
    """ Default monitor for P4KFiles extract methods """
    if exc_info:
        logger.exception(msg, exc_info=exc_info)
    else:
        logger.log(level, msg)


def extract_blueprint(blueprint: 'Blueprint', outdir: typing.Union[Path, str], exclude: typing.List[str] = None,
                      overwrite: bool = False, convert_cryxml_fmt: CryXmlConversionFormat = 'xml',
                      skip_lods: bool = True,
                      auto_unsplit_textures: bool = True, auto_convert_textures: bool = False,
                      report_tex_conversion_errors: bool = False, convert_dds_fmt: str = 'png',
                      extract_sounds: bool = True, auto_convert_models: bool = False,
                      cgf_converter_opts: str = CGF_CONVERTER_DEFAULT_OPTS, auto_convert_sounds: bool = False,
                      ww2ogg: str = '', revorb: str = '', cgf_converter_bin: str = '',
                      tex_converter: ConverterUtility = ConverterUtility.default,
                      tex_converter_bin: str = '', monitor=None, **kwargs
                      ) -> list:
    """
    Extract the required assets for this `Blueprint` to the given `outdir`.

    :param blueprint: The `Blueprint` to extract
    :param outdir: Root `Data` directory to extract into. It will be created if it does not exist.
    :param exclude: List of files to exclude from extraction
    :param overwrite: `Bool` whether or not to overwrite files within `outdir` if they already exist. If they
        already exist, and are not overwritten, they will be excluded from the returned list of extracted file paths
    :param convert_cryxml_fmt: Format to automatically convert CryXml binary data to during extraction.
        (Default: 'xml')
    :param skip_lods: Skip exporting/processing `_lod` files. (Default: True)
    :param auto_unsplit_textures: If True, will automatically combine `dds.N` files into a single texture
        (Default: False)
    :param auto_convert_textures: If True, `.dds` files will automatically be converted to `tif` files. This will
        forcefully enable `auto_unsplit_textures`. The original DDS file will also be extracted. (Default: False)
    :param report_tex_conversion_errors: By default, texture conversion errors will be silently ignored.
    :param convert_dds_fmt: The output format to convert DDS textures to. Default 'png'
    :param extract_sounds: If True, discover sound files are extracted and converted. The output files will contain
        the trigger name associated with the sound, and the wem_id of the sound file. There may be multiple sounds
        associated with each trigger name. (Default: True)
    :param auto_convert_models: If True, `cgf-converter` will be run on each extracted model file. (Default: False)
    :param cgf_converter_opts: Override the default flags passed to cgf_converter during model conversion.
    :param auto_convert_sounds: If True, `ww2ogg` and `revorb` will be run on each extracted wem. (Default: False)
    :param ww2ogg: Override which `ww2ogg` binary used for audio conversion. Will be auto-discovered by default.
    :param revorb: Override which `revorb` binary used for audio conversion. Will be auto-discovered by default.
    :param cgf_converter_bin: Override which `cgf-converter` binary used for model conversion.
        Will be auto-discovered by default.
    :param tex_converter: Override which texture converter utility used for texture conversion.
        Will be auto-discovered by default.
    :param tex_converter_bin: Override the texture converter executable to use.
        Will be auto-discovered by default.
    :param monitor: Callable function to output status messages to in addition to logging. See
        `scdatatools.p4k.P4KFile.extractall` for arguments `monitor` should accept
    :return: List of the extracted file paths on disk
    """
    monitor = monitor or _bp_monitor
    sc = blueprint.sc
    with log_time(f'extracting {blueprint.name}', monitor):
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)

        converters = [CryXmlConverter]
        converter_options = dict(**kwargs)
        converter_options.setdefault('convert_cryxml_fmt', convert_cryxml_fmt)

        logger.debug(f'{auto_convert_textures = }')
        if auto_convert_textures:
            converters.append(DDSTextureConverter)
            converter_options.setdefault('convert_dds_fmt', convert_dds_fmt)
            converter_options.setdefault('convert_dds_converter', tex_converter)
            converter_options.setdefault('convert_dds_converter_bin', tex_converter_bin)
            converter_options.setdefault('convert_dds_replace', True)

        logger.debug(f'{auto_convert_models = }')
        if auto_convert_models:
            converters.append(CGFModelConverter)
            converter_options.setdefault('cgf_converter_bin', cgf_converter_bin)
            converter_options.setdefault('cgf_converter_opts', cgf_converter_opts)

        files_to_extract = sc.p4k.search(blueprint.extract_filter, ignore_case=True, mode='in_strip', exclude=exclude)
        extracted_files = sc.p4k.extractall(outdir, files_to_extract, overwrite=overwrite,
                                            converters=converters, converter_options=converter_options,
                                            monitor=monitor)

        # write out any auto-converted files that may have been generated while processing
        for path, contents in blueprint.converted_files.items():
            outfile = outdir / path
            if not outfile.is_file() or overwrite:
                outfile.parent.mkdir(parents=True, exist_ok=True)
                with outfile.open('w') as out:
                    out.write(contents)

        # ############################################################################################################
        # # region process textures
        # if auto_convert_textures or auto_unsplit_textures:
        #     monitor('\n\nUn-splitting textures\n' + '-' * 80)
        #     found_textures = set()
        #     for dds_file in [_ for _ in extracted_files if '.dds' in _.lower()]:
        #         _ = Path(dds_file)
        #         if is_glossmap(dds_file):
        #             found_textures.add(outdir / _.parent / f'{_.name.split(".")[0]}.dds.a')
        #         else:
        #             found_textures.add(outdir / _.parent / f'{_.name.split(".")[0]}.dds')
        #
        #     def _do_unsplit(dds_file):
        #         msgs = []
        #         try:
        #             unsplit = collect_and_unsplit(Path(dds_file), outfile=Path(dds_file), remove=True)
        #             msgs.append((f'un-split {unsplit.relative_to(outdir)}', logging.INFO))
        #         except Exception as e:
        #             traceback.print_exc()
        #             return [(f'failed to un-split {dds_file}: {repr(e)}', logging.ERROR)]
        #
        #         try:
        #             if auto_convert_textures:
        #                 if is_glossmap(unsplit):
        #                     outfile = unsplit.with_name(f'{unsplit.name.split(".")[0]}.glossmap.{convert_dds_fmt}')
        #                 else:
        #                     outfile = unsplit.with_suffix(f'.{convert_dds_fmt}')
        #                 tex_convert(infile=unsplit, outfile=outfile, converter=tex_converter,
        #                             converter_bin=tex_converter_bin)
        #                 msgs.append(
        #                     (f'converted {unsplit.relative_to(outdir)} to {convert_dds_fmt}', logging.INFO))
        #         except Exception as e:
        #             traceback.print_exc()
        #             if report_tex_conversion_errors:
        #                 return [(f'failed to convert {dds_file}: {repr(e)}', logging.ERROR)]
        #         return msgs
        #
        #     with concurrent.futures.ThreadPoolExecutor() as executor:
        #         futures = [executor.submit(_do_unsplit, dds_file=_) for _ in found_textures]
        #         for future in concurrent.futures.as_completed(futures):
        #             for msg in future.result():
        #                 monitor(msg=msg[0], level=msg[1])
        # # endregion process textures
        # ############################################################################################################
        #
        # ############################################################################################################
        # # region convert models
        # cgf_converter = cgf_converter or CGF_CONVERTER
        # if auto_convert_textures and not cgf_converter:
        #     monitor(
        #         msg='\n\ncould not determine location of cgf-converter. Please ensure it can be found in system '
        #             'the path\n', level=logging.ERROR
        #     )
        # elif auto_convert_models:
        #     def _do_model_convert(model_file):
        #         cgf_cmd = f'"{cgf_converter}" {cgf_converter_opts} "{model_file}" -objectdir "{obj_dir}"'
        #         cgf = subprocess.Popen(cgf_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        #
        #         start_time = time.time()
        #         while (time.time() - start_time) < CGF_CONVERTER_TIMEOUT:
        #             if cgf.poll() is not None:
        #                 break
        #             time.sleep(1)
        #         else:
        #             # timed out, kill the process
        #             cgf.terminate()
        #         if cgf.returncode != 0:
        #             errmsg = cgf.stdout.read().decode('utf-8')
        #             if 'is being used by another process' in errmsg.lower():
        #                 return []  # someone else already picked up this file, ignore the error
        #             return [(f'model conversion failed for {model_file}: \n{errmsg}\n\n', logging.ERROR)]
        #         return [(f'converted {model_file}', logging.INFO)]
        #
        #     monitor(msg='\n\nConverting Models\n' + '-' * 80)
        #     obj_dir = outdir / 'Data'
        #     with concurrent.futures.ThreadPoolExecutor() as executor:
        #         futures = []
        #         for model_file in [_ for _ in extracted_files if _.split('.')[-1].lower() in CGF_CONVERTER_MODEL_EXTS]:
        #             model_file = outdir / Path(model_file)
        #             if model_file.suffix == '.cgf' and model_file.with_suffix('.cga').is_file():
        #                 continue  # skip converting cgf files if the cga equivalent is available
        #             futures.append(executor.submit(_do_model_convert, model_file=model_file))
        #         for future in concurrent.futures.as_completed(futures):
        #             for msg in future.result():
        #                 monitor(msg=msg[0], level=msg[1])
        # # endregion convert models
        # ############################################################################################################

        return extracted_files
