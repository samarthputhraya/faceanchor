"""Error types shared across the package.

Kept free of heavy imports so the CLI can catch a chain error without pulling
in web3 just to print help.
"""

from __future__ import annotations


class ChainError(RuntimeError):
    """A blockchain operation failed in a way the operator can act on."""
