"""Sheaf — open-source plural system tracking."""

import importlib.metadata

try:
    __version__ = importlib.metadata.version("sheaf")
except importlib.metadata.PackageNotFoundError:
    __version__ = "0.0.0-dev"
