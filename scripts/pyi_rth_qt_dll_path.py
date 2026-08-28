"""Prioritize the bundled Qt DLL directory before importing PySide6."""

from __future__ import annotations

import os
import sys
import ctypes


bundle_root = getattr(sys, "_MEIPASS", "")
qt_directory = os.path.join(bundle_root, "PySide6")
_runtime_dll_handles = []
for runtime_name in (
    "VCRUNTIME140.dll",
    "VCRUNTIME140_1.dll",
    "MSVCP140.dll",
    "MSVCP140_1.dll",
    "MSVCP140_2.dll",
    "CONCRT140.dll",
):
    runtime_path = os.path.join(bundle_root, runtime_name)
    if os.path.isfile(runtime_path):
        # Loading by absolute path before Qt imports prevents Windows from
        # satisfying the dependency with an older system-wide runtime.
        _runtime_dll_handles.append(ctypes.WinDLL(runtime_path))
if os.path.isdir(qt_directory):
    os.environ["PATH"] = qt_directory + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        # Keep the handle alive for the lifetime of the process.
        _qt_dll_directory_handle = os.add_dll_directory(qt_directory)
