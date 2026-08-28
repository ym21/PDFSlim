"""PySide6 desktop interface for PDFSlim."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Iterable

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from ..compression.analyzer import analyze, describe_pdf_error
from ..compression.pipeline import compress, output_name
from ..models.compression_result import CompressionResult
from ..models.compression_settings import COLOR_MODES, CompressionSettings, PRESETS


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024**2:
        return f"{size / 1024:.1f} KB"
    if size < 1024**3:
        return f"{size / 1024**2:.1f} MB"
    return f"{size / 1024**3:.2f} GB"


class Worker(QObject):
    """Run a batch away from the Qt GUI thread."""

    file_started = Signal(int, int, str)
    progress = Signal(int, int)
    file_finished = Signal(int, object)
    done = Signal(object)

    def __init__(self, files: list[Path], outdir: Path, settings: CompressionSettings) -> None:
        super().__init__()
        self.files = files
        self.outdir = outdir
        self.settings = settings
        self.cancelled = False

    @Slot()
    def run(self) -> None:
        results: list[tuple[int, CompressionResult]] = []
        total = len(self.files)
        try:
            for index, source in enumerate(self.files):
                if self.cancelled:
                    break
                self.file_started.emit(index, total, source.name)
                started = perf_counter()
                try:
                    destination = output_name(source, self.outdir)
                    result = compress(
                        source,
                        destination,
                        self.settings,
                        cancel=lambda: self.cancelled,
                        progress=lambda current, pages: self.progress.emit(current, pages),
                    )
                except Exception as exc:
                    # Keep one unexpected queue error from aborting the rest of
                    # the batch.  The normal pipeline already returns this
                    # object for pikepdf/Pillow failures.
                    try:
                        original_size = source.stat().st_size
                    except OSError:
                        original_size = 0
                    result = CompressionResult(
                        source_path=source,
                        output_path=self.outdir / f"{source.stem}_compressed.pdf",
                        original_size=original_size,
                        compressed_size=0,
                        reduction_ratio=0.0,
                        elapsed_seconds=perf_counter() - started,
                        success=False,
                        error_message=describe_pdf_error(exc),
                    )
                results.append((index, result))
                self.file_finished.emit(index, result)
                if self.cancelled:
                    break
        finally:
            self.done.emit(results)

    @Slot()
    def cancel(self) -> None:
        self.cancelled = True


class MainWindow(QMainWindow):
    """Main queue, settings, and progress view."""

    COLUMNS = ("ファイル名", "元サイズ", "ページ", "状態", "圧縮後", "削減率", "進捗")

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PDFSlim")
        self.resize(980, 720)
        self.setAcceptDrops(True)
        self.files: list[Path] = []
        self.outdir = Path.home() / "Compressed"
        self.thread: QThread | None = None
        self.worker: Worker | None = None
        self._active_index = -1
        self._completed_indexes: set[int] = set()
        self._batch_cancelled = False
        self._build_ui()
        self.apply_preset(self.preset.currentText())

    def _build_ui(self) -> None:
        root = QWidget(self)
        layout = QVBoxLayout(root)

        toolbar = QHBoxLayout()
        add_files = QPushButton("PDFを追加")
        add_files.clicked.connect(self.add_files)
        add_folder = QPushButton("フォルダから追加")
        add_folder.clicked.connect(self.add_folder)
        remove = QPushButton("選択を削除")
        remove.clicked.connect(self.remove_selected)
        clear = QPushButton("すべて削除")
        clear.clicked.connect(self.clear_files)
        open_destination = QPushButton("保存先を開く")
        open_destination.clicked.connect(self.open_destination)
        self._queue_controls = [add_files, add_folder, remove, clear, open_destination]
        for button in self._queue_controls:
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.file_table = QTableWidget(0, len(self.COLUMNS))
        self.file_table.setHorizontalHeaderLabels(self.COLUMNS)
        self.file_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.file_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_table.setAlternatingRowColors(True)
        header = self.file_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(self.COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.file_table, 1)
        # Compatibility aliases for the first MVP's public widget attributes.
        self.list = self.file_table

        settings_box = QGroupBox("圧縮設定")
        self.settings_box = settings_box
        settings_layout = QVBoxLayout(settings_box)
        basic = QFormLayout()
        self.preset = QComboBox()
        self.preset.addItems(list(PRESETS))
        self.preset.currentTextChanged.connect(self.apply_preset)
        basic.addRow("プリセット", self.preset)

        self.dpi = QComboBox()
        self.dpi.addItem("元解像度を維持", None)
        for value in (300, 200, 150, 120, 96):
            self.dpi.addItem(f"{value} DPI", value)
        self.dpi.setEditable(True)
        self.dpi.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.dpi.setToolTip("元画像の実効DPIがこの値より高い場合だけ縮小します。低解像度画像は拡大しません。")
        basic.addRow("解像度", self.dpi)

        quality_row = QHBoxLayout()
        self.quality = QSlider(Qt.Orientation.Horizontal)
        self.quality.setRange(1, 100)
        self.quality.valueChanged.connect(self._update_quality_label)
        self.quality_label = QLabel()
        quality_row.addWidget(self.quality, 1)
        quality_row.addWidget(self.quality_label)
        basic.addRow("JPEG品質 (1-100)", quality_row)

        self.mode = QComboBox()
        self.mode.addItems(list(COLOR_MODES))
        basic.addRow("カラーモード", self.mode)
        settings_layout.addLayout(basic)

        detail_box = QGroupBox("詳細設定")
        detail_layout = QVBoxLayout(detail_box)
        self.downsample_check = QCheckBox("ダウンサンプリング")
        self.jpeg_check = QCheckBox("JPEG再圧縮")
        self.crop_check = QCheckBox("自動余白除去")
        self.optimize_check = QCheckBox("PDF内部最適化")
        self.metadata_check = QCheckBox("メタデータ削除")
        for check in (
            self.downsample_check,
            self.jpeg_check,
            self.crop_check,
            self.optimize_check,
            self.metadata_check,
        ):
            detail_layout.addWidget(check)
        settings_layout.addWidget(detail_box)
        layout.addWidget(settings_box)

        destination_row = QHBoxLayout()
        destination_row.addWidget(QLabel("保存先"))
        self.dest = QLineEdit(str(self.outdir))
        self.dest.setReadOnly(True)
        destination_row.addWidget(self.dest, 1)
        choose = QPushButton("保存先を変更")
        choose.clicked.connect(self.choose_dir)
        self._queue_controls.append(choose)
        destination_row.addWidget(choose)
        layout.addLayout(destination_row)

        progress_box = QGroupBox("進捗")
        progress_layout = QVBoxLayout(progress_box)
        self.current_file = QLabel("待機中")
        self.page_label = QLabel("ページ: -")
        progress_layout.addWidget(self.current_file)
        progress_layout.addWidget(self.page_label)
        self.file_progress = QProgressBar()
        self.file_progress.setRange(0, 100)
        self.file_progress.setFormat("ファイル %p%")
        progress_layout.addWidget(self.file_progress)
        self.overall_progress = QProgressBar()
        self.overall_progress.setRange(0, 100)
        self.overall_progress.setFormat("全体 %p%")
        progress_layout.addWidget(self.overall_progress)
        layout.addWidget(progress_box)
        self.progress = self.overall_progress

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.start = QPushButton("圧縮開始")
        self.start.clicked.connect(self.start_jobs)
        self.cancel = QPushButton("キャンセル")
        self.cancel.clicked.connect(self.cancel_jobs)
        self.cancel.setEnabled(False)
        action_row.addWidget(self.start)
        action_row.addWidget(self.cancel)
        layout.addLayout(action_row)

        self.setCentralWidget(root)
        self.statusBar().showMessage("PDFを追加してください。ドラッグ&ドロップにも対応しています。")

    @staticmethod
    def _valid_paths(paths: Iterable[Path]) -> list[Path]:
        return [path for path in paths if path.suffix.lower() == ".pdf"]

    def _append_paths(self, paths: Iterable[Path]) -> None:
        if self.thread and self.thread.isRunning():
            return
        existing = {str(path.resolve()).casefold() for path in self.files}
        for path in self._valid_paths(paths):
            try:
                key = str(path.resolve()).casefold()
            except OSError:
                key = str(path.absolute()).casefold()
            if key in existing:
                continue
            existing.add(key)
            self.files.append(path)
            self._add_file_row(path)
        self.statusBar().showMessage(f"{len(self.files)} 件のPDFをキューに追加しました。")

    def _add_file_row(self, path: Path) -> None:
        metadata = analyze(path)
        row = self.file_table.rowCount()
        self.file_table.insertRow(row)
        values = (
            path.name,
            _format_size(int(metadata.get("size", 0))),
            str(metadata.get("pages", "-")) if not metadata.get("error") else "-",
            "エラー" if metadata.get("error") else "待機中",
            "-",
            "-",
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column == 0:
                item.setToolTip(str(path))
                item.setData(Qt.ItemDataRole.UserRole, str(path))
            if metadata.get("error"):
                item.setToolTip(str(metadata["error"]))
            self.file_table.setItem(row, column, item)
        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(0)
        progress.setFormat("%p%")
        self.file_table.setCellWidget(row, 6, progress)

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "PDFを追加", filter="PDF (*.pdf)")
        self._append_paths(Path(path) for path in paths)

    def add_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "PDFフォルダを選択")
        if directory:
            self._append_paths(sorted(Path(directory).iterdir()))

    def remove_selected(self) -> None:
        if self.thread and self.thread.isRunning():
            return
        rows = sorted({index.row() for index in self.file_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.file_table.removeRow(row)
            del self.files[row]
        if rows:
            self.statusBar().showMessage(f"{len(self.files)} 件のPDFがキューに残っています。")

    def clear_files(self) -> None:
        if self.thread and self.thread.isRunning():
            return
        self.file_table.setRowCount(0)
        self.files.clear()
        self.overall_progress.setValue(0)
        self.file_progress.setValue(0)
        self.current_file.setText("待機中")

    def choose_dir(self) -> None:
        if self.thread and self.thread.isRunning():
            return
        directory = QFileDialog.getExistingDirectory(self, "保存先")
        if directory:
            self.outdir = Path(directory)
            self.dest.setText(str(self.outdir))

    def open_destination(self) -> None:
        self.outdir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.outdir)))

    def _update_quality_label(self, value: int) -> None:
        self.quality_label.setText(str(value))

    def _set_checkbox(self, check: QCheckBox, value: bool) -> None:
        check.setChecked(bool(value))

    @Slot(str)
    def apply_preset(self, name: str) -> None:
        if name not in PRESETS:
            return
        settings = CompressionSettings.from_preset(name)
        if settings.target_dpi is None:
            self.dpi.setCurrentIndex(0)
        else:
            self.dpi.setCurrentText(str(settings.target_dpi))
        self.quality.setValue(settings.jpeg_quality)
        self.mode.setCurrentText(settings.color_mode)
        self._set_checkbox(self.downsample_check, settings.enable_downsampling)
        self._set_checkbox(self.jpeg_check, settings.enable_jpeg_recompression)
        self._set_checkbox(self.crop_check, settings.enable_auto_crop)
        self._set_checkbox(self.optimize_check, settings.enable_pdf_optimization)
        self._set_checkbox(self.metadata_check, settings.remove_metadata)

    def _dpi_value(self) -> int | None:
        text = self.dpi.currentText().strip()
        if text in ("", "元解像度を維持"):
            return None
        try:
            value = int(text.split()[0])
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def _current_settings(self) -> CompressionSettings:
        return CompressionSettings(
            preset=self.preset.currentText(),
            target_dpi=self._dpi_value(),
            jpeg_quality=self.quality.value(),
            color_mode=self.mode.currentText(),
            enable_downsampling=self.downsample_check.isChecked(),
            enable_jpeg_recompression=self.jpeg_check.isChecked(),
            enable_grayscale=False,
            enable_binarization=False,
            enable_auto_crop=self.crop_check.isChecked(),
            enable_pdf_optimization=self.optimize_check.isChecked(),
            remove_metadata=self.metadata_check.isChecked(),
        )

    def _row_progress(self, row: int) -> QProgressBar | None:
        widget = self.file_table.cellWidget(row, 6)
        return widget if isinstance(widget, QProgressBar) else None

    def _set_row_value(self, row: int, column: int, value: str, tooltip: str | None = None) -> None:
        item = self.file_table.item(row, column)
        if item is None:
            item = QTableWidgetItem()
            self.file_table.setItem(row, column, item)
        item.setText(value)
        if tooltip:
            item.setToolTip(tooltip)

    def _set_running(self, running: bool) -> None:
        self.start.setEnabled(not running)
        self.cancel.setEnabled(running)
        for control in self._queue_controls:
            control.setEnabled(not running)
        self.file_table.setEnabled(not running)
        self.settings_box.setEnabled(not running)

    def start_jobs(self) -> None:
        if self.thread and self.thread.isRunning():
            return
        if not self.files:
            QMessageBox.information(self, "PDFSlim", "先にPDFを追加してください。")
            return
        settings = self._current_settings()
        self._completed_indexes.clear()
        self._batch_cancelled = False
        self.overall_progress.setValue(0)
        self.file_progress.setValue(0)
        for row in range(self.file_table.rowCount()):
            self._set_row_value(row, 3, "待機中")
            self._set_row_value(row, 4, "-")
            self._set_row_value(row, 5, "-")
            progress = self._row_progress(row)
            if progress:
                progress.setValue(0)

        self.thread = QThread(self)
        self.worker = Worker(list(self.files), self.outdir, settings)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.file_started.connect(self._on_file_started)
        self.worker.progress.connect(self._on_page_progress)
        self.worker.file_finished.connect(self._on_file_finished)
        self.worker.done.connect(self._on_batch_done)
        self.worker.done.connect(self.thread.quit)
        self.worker.done.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._on_thread_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self._set_running(True)
        self.thread.start()

    @Slot(int, int, str)
    def _on_file_started(self, index: int, total: int, name: str) -> None:
        self._active_index = index
        self.current_file.setText(f"処理中: {name}")
        self.page_label.setText("ページ: 0 / -")
        self.file_progress.setValue(0)
        if index < self.file_table.rowCount():
            self._set_row_value(index, 3, "処理中")
            progress = self._row_progress(index)
            if progress:
                progress.setValue(0)
        self.statusBar().showMessage(f"{index + 1} / {total}: {name}")

    @Slot(int, int)
    def _on_page_progress(self, current: int, total: int) -> None:
        if total <= 0:
            fraction = 1.0
        else:
            fraction = min(1.0, max(0.0, current / total))
        self.file_progress.setValue(round(fraction * 100))
        self.page_label.setText(f"ページ: {current} / {total}")
        if 0 <= self._active_index < self.file_table.rowCount():
            progress = self._row_progress(self._active_index)
            if progress:
                progress.setValue(round(fraction * 100))
        total_files = len(self.files)
        overall = ((self._active_index + fraction) / total_files) if total_files else 0.0
        self.overall_progress.setValue(round(min(1.0, overall) * 100))

    @Slot(int, object)
    def _on_file_finished(self, index: int, result: CompressionResult) -> None:
        self._completed_indexes.add(index)
        if index >= self.file_table.rowCount():
            return
        progress = self._row_progress(index)
        if result.success:
            if progress:
                progress.setValue(100)
            status = "完了 (警告)" if result.warning_message else "完了"
            self._set_row_value(index, 3, status, result.warning_message)
            self._set_row_value(index, 4, _format_size(result.compressed_size), str(result.output_path))
            self._set_row_value(index, 5, f"{result.reduction_percent:.1f}%")
        else:
            status = "キャンセル" if result.error_message == "キャンセルされました。" else "失敗"
            self._set_row_value(index, 3, status, result.error_message)
            self._set_row_value(index, 4, "-")
            self._set_row_value(index, 5, "-")

    @Slot(object)
    def _on_batch_done(self, results: list[tuple[int, CompressionResult]]) -> None:
        failed = sum(1 for _, result in results if not result.success and result.error_message != "キャンセルされました。")
        if self._batch_cancelled:
            self.current_file.setText("キャンセルしました")
            self.statusBar().showMessage("処理をキャンセルしました。生成途中のファイルは削除されています。")
        else:
            self.current_file.setText("処理完了")
            self.overall_progress.setValue(100)
            self.statusBar().showMessage(
                f"{len(results)} 件を処理しました。失敗: {failed} 件。"
            )

    def _on_thread_finished(self) -> None:
        if self._batch_cancelled:
            for index in range(len(self.files)):
                if index not in self._completed_indexes and index < self.file_table.rowCount():
                    self._set_row_value(index, 3, "キャンセル")
        self.worker = None
        self.thread = None
        self._active_index = -1
        self._set_running(False)

    def cancel_jobs(self) -> None:
        if self.worker:
            self._batch_cancelled = True
            self.worker.cancel()
            self.cancel.setEnabled(False)
            self.statusBar().showMessage("キャンセル中...現在の画像処理が終わるまで待機しています。")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and any(
            Path(url.toLocalFile()).suffix.lower() == ".pdf"
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        self._append_paths(paths)
        event.acceptProposedAction()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.thread and self.thread.isRunning():
            if self.worker:
                self.worker.cancel()
            if not self.thread.wait(5000):
                QMessageBox.warning(self, "PDFSlim", "処理中のため終了できません。キャンセル後に再試行してください。")
                event.ignore()
                return
        event.accept()
