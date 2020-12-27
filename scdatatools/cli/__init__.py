import sys

from nubia import Nubia, Options

from . import commands


def main():
    shell = Nubia(
        name="scdt", command_pkgs=commands, options=Options(persistent_history=False),
    )
    args = sys.argv
    if '-s' not in args:
        args.insert(1, '-s')
    sys.exit(shell.run(args))
