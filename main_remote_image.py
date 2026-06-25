"""Restricted remote-image variant entry point."""

import sys

import viewer.config as config
from viewer.security import NetworkMode


config.NETWORK_MODE = NetworkMode.RESTRICTED_REMOTE_IMAGES
config.ICON_NAME = "email_red.png" if sys.platform.startswith("linux") else "email_red.ico"

from main import main  # noqa: E402 - configuration must precede the GUI import


if __name__ == "__main__":
    sys.exit(main())

