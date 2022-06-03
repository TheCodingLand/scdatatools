
import argparse

from nubia import CompletionDataSource, PluginInterface


class SCDTNubiaPlugin(PluginInterface):
    """
    The PluginInterface class is a way to customize nubia for every customer
    use case. It allowes custom argument validation, control over command
    loading, custom context objects, and much more.
    """

    # def create_context(self):
    #     """
    #     Must create an object that inherits from `Context` parent class.
    #     The plugin can return a custom context but it has to inherit from the
    #     correct parent class.
    #     """
    #     return NubiaContext()

    def validate_args(self, args):
        """
        This will be executed when starting nubia, the args passed is a
        dict-like object that contains the argparse result after parsing the
        command line arguments. The plugin can choose to update the context
        with the values, and/or decide to raise `ArgsValidationError` with
        the error message.
        """
        pass

    def get_opts_parser(self, add_help=True):
        """
        Builds the ArgumentParser that will be passed to , use this to
        build your list of arguments that you want for your shell.
        """
        opts_parser = argparse.ArgumentParser(
            description="scdt - Star Citizen Data Tools",
            formatter_class=argparse.ArgumentDefaultsHelpFormatter,
            add_help=add_help,
        )
        # opts_parser.add_argument(
        #     "--config", "-c", default="", type=str, help="Configuration File"
        # )
        opts_parser.add_argument(
            "--verbose",
            "-v",
            action="count",
            default=0,
            help="Increase verbosity, can be specified multiple times",
        )
        opts_parser.add_argument(
            "--stderr",
            "-s",
            action="store_true",
            help="By default the logging output goes to a "
                 "temporary file. This disables this feature "
                 "by sending the logging output to stderr",
        )
        return opts_parser

    # def create_usage_logger(self, context):
    #     """
    #     Override this and return you own usage logger.
    #     Must be a subtype of UsageLoggerInterface.
    #     """
    #     return None
    #
    # def get_status_bar(self, context):
    #     """
    #     This returns the StatusBar object that handles the bottom status bar
    #     and the right-side per-line status
    #     """
    #     return NubiaExampleStatusBar(context)
    #
    # def getBlacklistPlugin(self):
    #     blacklister = CommandBlacklist()
    #     blacklister.add_blocked_command("be-blocked")
    #     return blacklister