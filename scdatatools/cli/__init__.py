import sys

from nubia import Nubia, Options

from scdatatools import __version__
from scdatatools.cli import commands
from scdatatools.plugins import plugin_manager

from .plugin import SCDTNubiaPlugin


def main():
    plugin_manager.setup()
    shell = Nubia(
        name="scdt",
        plugin=SCDTNubiaPlugin(),
        command_pkgs=commands,
        options=Options(persistent_history=False),
    )
    args = sys.argv
    if "-s" not in args:
        args.insert(1, "-s")
    sys.exit(shell.run(args))
