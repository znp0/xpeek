"""PySide6 GUI overlay for the translator."""
from __future__ import annotations

import sys
import os
import signal
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QTextEdit
)
from PySide6.QtCore import QThread, Signal, Qt, QTimer
from PySide6.QtGui import QFont

from .config import Config
from .capture import capture_region, CaptureError
from .ocr import extract_text, OcrError
from .providers import get_translator, TranslationError
from .history import HistoryStore

class TranslationWorker(QThread):
    finished_signal = Signal(str, str, str)  # error_msg, original_text, translated_text

    def __init__(self, config: Config):
        super().__init__()
        self.config = config

    def run(self):
        try:
            image_path = capture_region(self.config.region)
        except CaptureError as exc:
            self.finished_signal.emit(f"Capture error: {exc}", "", "")
            return

        try:
            ocr_text = extract_text(image_path)
        except OcrError as exc:
            self.finished_signal.emit(f"OCR error: {exc}", "", "")
            return
        finally:
            image_path.unlink(missing_ok=True)

        if not ocr_text:
            self.finished_signal.emit("", "", "")
            return

        # Game dialogs often span multiple lines but form a single sentence.
        # Replacing newlines with spaces prevents the translation provider
        # from translating them as separate fragments.
        ocr_text = ocr_text.replace("\n", " ").strip()

        try:
            provider = get_translator(self.config.provider, self.config.provider_options)
            translated = provider.translate(
                ocr_text, self.config.source_lang, self.config.target_lang
            )
        except TranslationError as exc:
            self.finished_signal.emit(f"Translation error: {exc}", ocr_text, "")
            return

        history = HistoryStore(limit=self.config.history_limit)
        history.add(ocr_text, translated)

        self.finished_signal.emit("", ocr_text, translated)


class OverlayWindow(QWidget):
    def __init__(self, config: Config, mode: str = "translate"):
        super().__init__()
        self.config = config

        self.setWindowTitle("xpeek")

        self.setStyleSheet("""
            QWidget {
                background-color: #101010;
                color: #ffffff;
            }
            QTextEdit {
                background-color: transparent;
                border: none;
                padding: 10px;
            }
        """)

        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.resize(500, 400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(QFont("Arial", 14))
        layout.addWidget(self.text_edit)

        self.worker = TranslationWorker(config)
        self.worker.finished_signal.connect(self.on_translation_finished)

        self.handle_mode(mode)

    def handle_mode(self, mode: str):
        if self.isVisible():
            self.close()
            return

        self.show()

        if mode == "translate":
            self.text_edit.setPlainText("...")
            if not getattr(self, 'worker', None) or not self.worker.isRunning():
                self.worker.start()
        elif mode == "last":
            history = HistoryStore(limit=self.config.history_limit)
            entries = history.all()
            if entries:
                self.text_edit.setPlainText(entries[-1].translation)
            else:
                self.text_edit.setPlainText("No translation history.")

    def closeEvent(self, event):
        # Dismiss immediately, but let an active worker finish before Qt exits.
        # Destroying a running QThread would abort the process.
        event.accept()
        app = QApplication.instance()
        self.worker.finished.connect(app.quit)
        if not self.worker.isRunning():
            app.quit()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPosition().toPoint()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            diff = event.globalPosition().toPoint() - self.drag_pos
            self.move(self.pos() + diff)
            self.drag_pos = event.globalPosition().toPoint()
            event.accept()

    def on_translation_finished(self, error: str, original: str, translated: str):
        if error:
            self.text_edit.setPlainText(f"Error:\n{error}")
        elif not original:
            self.text_edit.setPlainText("Could not detect any text.")
        else:
            self.text_edit.setPlainText(translated)


# Global flags for signal handling
_TRIGGER_TRANSLATE = False
_TRIGGER_LAST = False

def _sig_translate(signum, frame):
    global _TRIGGER_TRANSLATE
    _TRIGGER_TRANSLATE = True

def _sig_last(signum, frame):
    global _TRIGGER_LAST
    _TRIGGER_LAST = True

def run_gui_translation(config: Config, mode: str = "translate") -> int:
    os.environ["QT_QPA_PLATFORM"] = "wayland"

    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)

    app.setDesktopFileName("xpeek")
    app.setApplicationName("xpeek")
    app.setStyle("Fusion")

    # closeEvent quits once any active translation worker has finished.
    app.setQuitOnLastWindowClosed(False)

    window = OverlayWindow(config, mode)

    signal.signal(signal.SIGUSR1, _sig_translate)
    signal.signal(signal.SIGUSR2, _sig_last)

    # Polling timer for signals
    timer = QTimer()
    def check_signals():
        global _TRIGGER_TRANSLATE, _TRIGGER_LAST
        if _TRIGGER_TRANSLATE:
            _TRIGGER_TRANSLATE = False
            window.close()
        if _TRIGGER_LAST:
            _TRIGGER_LAST = False
            window.close()

    timer.timeout.connect(check_signals)
    timer.start(50)

    return app.exec()
