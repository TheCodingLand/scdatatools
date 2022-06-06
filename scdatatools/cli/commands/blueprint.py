import sys
import shutil
import typing
from pathlib import Path

from nubia import command, argument

from . import common


@command(aliases=['bp'])
class blueprint:
    """Generate and extract STar Citizen Blueprint (scbp)."""

    @command()
    @common.sc_dir_argument
    def generate(self,
                 sc_dir: str
                 ):
        """ Generate a scbp """
        sc = common.open_sc_dir(sc_dir)
        print(sc_dir, sc)

    @command()
    @common.sc_dir_argument
    @common.extraction_args(exclude=['extract_model_assets'])
    def extract(self,
                sc_dir: str,
                convert_cryxml: typing.Text = "",
                unsplit_textures: bool = False,
                convert_textures: str = "",
                convert_models: bool = False,
                no_overwrite: bool = False,
                output: typing.Text = ".",
                ):
        """ Extract all the record assets for a given blueprint, optionally also generating the blueprint. """
        sc = common.open_sc_dir(sc_dir)
        print(sc_dir, sc)
