"""Runtime configuration selected by the executable entry point."""

import sys

from viewer.security import DEFAULT_REMOTE_FETCH_POLICY, NetworkMode, RemoteFetchPolicy


NETWORK_MODE: NetworkMode = NetworkMode.OFFLINE
REMOTE_FETCH_POLICY: RemoteFetchPolicy = DEFAULT_REMOTE_FETCH_POLICY
ICON_NAME: str = "email_blue.png" if sys.platform.startswith("linux") else "email_blue.ico"

