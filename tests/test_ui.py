from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from pdfslim.ui.main_window import MainWindow


def test_color_conversion_controls_are_not_duplicated() -> None:
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        assert window.mode.count() == 3
        assert not hasattr(window, "gray_check")
        assert not hasattr(window, "binary_check")
    finally:
        window.close()
        application.processEvents()
