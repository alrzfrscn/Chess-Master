"""Chess Master — a precision chess desktop studio.

The package is intentionally split into four layers:

* ``engine``  — authoritative game state, PGN/FEN parsing and chess rules.
* ``analysis`` — evaluation math shared by the future review engine.
* ``bridge``  — the PyWebView JS API and the local asset server.
* ``models``  — small, serializable domain objects.

Frontend assets live in ``chess_master.web`` so the desktop application can be
packaged as a single Python distribution.
"""

__all__ = ["__version__"]

__version__ = "0.2.0"
