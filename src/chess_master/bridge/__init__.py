"""Bridge layer: the PyWebView API and the local asset server."""

from chess_master.bridge.api import ChessMasterApi
from chess_master.bridge.web_server import WebAppServer

__all__ = ["ChessMasterApi", "WebAppServer"]
