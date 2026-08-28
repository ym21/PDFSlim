"""Prioritize the bundled Qt DLL directory before importing PySide6."""

from __future__ import annotations

import os
import sys


bundle_root = getattr(sys, "_MEIPASS", "")
qt_directory = os.path.join(bundle_root, "PySide6")
if os.path.isdir(qt_directory):
    os.environ["PATH"] = qt_directory + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        # Keep the handle alive for the lifetime of the process.
        _qt_dll_directory_handle = os.add_dll_directory(qt_directory)
