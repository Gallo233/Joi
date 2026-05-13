from __future__ import annotations

import json
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QRect, QSize, Qt, QThreadPool, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app2.codex_runner import CodexRunner
from app2.events import AppEvent, RouteKind, TaskStatus, voice_line
from app2.router import route_user_text
from mvp.config import AppConfig, CharacterConfig, SceneConfig
from mvp.tts import GptSoVitsClient


class CharacterStage(QWidget):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self._character = config.primary_character
        self._scene = config.scene
        self._sprite_id = "1"
        self._background = self._load_pixmap(config.scene.background_image_path)
        self._sprite = self._load_pixmap(config.primary_character.sprite_image_path(self._sprite_id))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_character(self, character: CharacterConfig, sprite_id: str = "1") -> None:
        self._character = character
        self._sprite_id = sprite_id
        self._sprite = self._load_pixmap(character.sprite_image_path(sprite_id))
        self.update()

    def set_scene(self, scene: SceneConfig) -> None:
        self._scene = scene
        self._background = self._load_pixmap(scene.background_image_path)
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_background(painter)
        self._paint_sprite(painter)

    def _paint_background(self, painter: QPainter) -> None:
        painter.fillRect(self.rect(), QColor("#f7f7f4"))
        if not self._background.isNull():
            scaled = self._background.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = max(0, (scaled.width() - self.width()) // 2)
            y = max(0, (scaled.height() - self.height()) // 2)
            painter.drawPixmap(self.rect(), scaled, QRect(x, y, self.width(), self.height()))
            painter.fillRect(self.rect(), QColor(255, 255, 255, 112))

    def _paint_sprite(self, painter: QPainter) -> None:
        if self._sprite.isNull():
            self._paint_placeholder(painter)
            return
        max_size = QSize(int(self.width() * 0.54), int(self.height() * 0.94))
        scaled = self._sprite.scaled(max_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        x = (self.width() - scaled.width()) // 2
        y = max(0, self.height() - scaled.height() - 22)
        painter.drawPixmap(x, y, scaled)

    def _paint_placeholder(self, painter: QPainter) -> None:
        color = QColor(self._character.sprite_color or "#224e66")
        w = min(260, max(160, self.width() // 3))
        h = min(340, max(220, self.height() - 120))
        x = (self.width() - w) // 2
        y = self.height() - h - 42
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color.darker(120))
        painter.drawRoundedRect(x, y + 68, w, h - 68, 38, 38)
        painter.setBrush(color.lighter(132))
        painter.drawEllipse(x + w // 4, y, w // 2, w // 2)
        painter.setPen(QPen(QColor("#ffffff"), 2))
        painter.drawText(self.rect().adjusted(0, 0, 0, -24), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, self._character.name)

    def _load_pixmap(self, path: str) -> QPixmap:
        if not path:
            return QPixmap()
        return QPixmap(str(self._config.resolve_path(path)))


class CodexSignals(QObject):
    event = Signal(object)
    finished = Signal()


class CodexJob(QRunnable):
    def __init__(self, runner: CodexRunner, goal: str) -> None:
        super().__init__()
        self._runner = runner
        self._goal = goal
        self.signals = CodexSignals()

    @Slot()
    def run(self) -> None:
        try:
            self._runner.run(self._goal, self.signals.event.emit)
        except Exception:
            traceback.print_exc()
        finally:
            self.signals.finished.emit()


class TtsSignals(QObject):
    finished = Signal(int, str)
    failed = Signal(int, str)


class TtsJob(QRunnable):
    def __init__(self, generation_id: int, client: GptSoVitsClient, text: str, character: CharacterConfig, sprite_id: str) -> None:
        super().__init__()
        self._generation_id = generation_id
        self._client = client
        self._text = text
        self._character = character
        self._sprite_id = sprite_id
        self.signals = TtsSignals()

    @Slot()
    def run(self) -> None:
        try:
            path = self._client.synthesize(self._text, self._character, self._sprite_id)
            self.signals.finished.emit(self._generation_id, str(path))
        except Exception as exc:
            self.signals.failed.emit(self._generation_id, str(exc))


class Mvp2Window(QWidget):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self._pool = QThreadPool.globalInstance()
        self._runner = CodexRunner(config)
        self._history_path = config.base_dir / "data" / "app2" / "history.json"
        self._history_rows: list[dict[str, str]] = self._load_history()
        self._busy = False
        self._tts_client = self._create_tts_client()
        self._tts_generation_id = 0
        self._audio_player = None
        self._audio_output = None
        self._create_audio_player()

        self.setWindowTitle("Shinsekai MVP2")
        self.resize(900, 680)
        flags = self.windowFlags()
        if config.app.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

        self.stage = CharacterStage(config)
        self.stage.setParent(self)

        self.top_hint = QLabel("MVP2 · Codex 执行器模式")
        self.top_hint.setObjectName("topHint")
        self.top_hint.setParent(self)

        self.task_card = QFrame(self)
        self.task_card.setObjectName("taskCard")
        task_layout = QVBoxLayout(self.task_card)
        task_layout.setContentsMargins(14, 12, 14, 12)
        task_layout.setSpacing(8)
        self.task_title = QLabel("Codex 任务")
        self.task_title.setObjectName("taskTitle")
        self.task_status = QLabel("等待任务")
        self.task_goal = QLabel("")
        self.task_goal.setWordWrap(True)
        self.task_details = QPlainTextEdit()
        self.task_details.setReadOnly(True)
        self.task_details.setObjectName("taskDetails")
        task_layout.addWidget(self.task_title)
        task_layout.addWidget(self.task_status)
        task_layout.addWidget(self.task_goal)
        task_layout.addWidget(self.task_details, 1)
        self.task_card.hide()

        self.dialog_card = QFrame(self)
        self.dialog_card.setObjectName("dialogCard")
        dialog_layout = QVBoxLayout(self.dialog_card)
        dialog_layout.setContentsMargins(20, 14, 20, 16)
        dialog_layout.setSpacing(8)
        self.name_label = QLabel(config.primary_character.name)
        self.name_label.setObjectName("nameLabel")
        self.speech_label = QLabel("我在。把要处理的工程任务直接告诉我就好。")
        self.speech_label.setObjectName("speechLabel")
        self.speech_label.setWordWrap(True)
        dialog_layout.addWidget(self.name_label)
        dialog_layout.addWidget(self.speech_label, 1)

        self.input_frame = QFrame(self)
        self.input_frame.setObjectName("inputFrame")
        input_layout = QHBoxLayout(self.input_frame)
        input_layout.setContentsMargins(12, 6, 8, 6)
        input_layout.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入任务，例如：修复项目里的 bug 并跑测试")
        self.input.returnPressed.connect(self._submit)
        self.send_button = QPushButton("发送")
        self.send_button.clicked.connect(self._submit)
        input_layout.addWidget(self.input, 1)
        input_layout.addWidget(self.send_button)

        self._apply_style()
        self._append_history("系统", "MVP2 已启动。")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        self.stage.setGeometry(0, 0, w, h)
        self.top_hint.adjustSize()
        self.top_hint.move(max(16, (w - self.top_hint.width()) // 2), 12)

        task_w = min(430, max(330, int(w * 0.42)))
        task_h = min(310, max(210, int(h * 0.42)))
        self.task_card.setGeometry(18, 54, task_w, task_h)

        dialog_w = min(660, max(500, int(w * 0.68)))
        input_h = 48
        dialog_h = 118
        x = (w - dialog_w) // 2
        input_y = h - input_h - 24
        self.dialog_card.setGeometry(x, input_y - dialog_h - 12, dialog_w, dialog_h)
        self.input_frame.setGeometry(x, input_y, dialog_w, input_h)

    def _apply_style(self) -> None:
        accent = self._config.primary_character.color or "#e5a3a3"
        self.setStyleSheet(
            f"""
            QLabel#topHint {{
                color: white;
                background: rgba(60, 60, 65, 150);
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 12px;
            }}
            QFrame#dialogCard, QFrame#taskCard {{
                background: rgba(36, 36, 40, 178);
                border: 1px solid rgba(255, 255, 255, 42);
                border-radius: 8px;
            }}
            QLabel#nameLabel, QLabel#taskTitle {{
                color: {accent};
                font-weight: 700;
                background: transparent;
            }}
            QLabel#speechLabel, QLabel {{
                color: #ffffff;
                background: transparent;
                font-size: 14px;
            }}
            QPlainTextEdit#taskDetails {{
                color: #202124;
                background: rgba(255, 255, 255, 218);
                border: 0;
                border-radius: 6px;
                padding: 8px;
                font-size: 12px;
            }}
            QFrame#inputFrame {{
                background: rgba(255, 255, 255, 236);
                border-radius: 8px;
            }}
            QLineEdit {{
                border: 0;
                background: transparent;
                color: #202124;
                font-size: 14px;
            }}
            QPushButton {{
                border: 0;
                border-radius: 16px;
                padding: 7px 17px;
                color: white;
                background: {accent};
                font-weight: 700;
            }}
            QPushButton:disabled {{
                background: #b8b8b8;
            }}
            """
        )

    def _submit(self) -> None:
        text = self.input.text().strip()
        if not text or self._busy:
            return
        self.input.clear()
        self._append_history("你", text)
        decision = route_user_text(text)
        if decision.kind == RouteKind.CODEX:
            self._start_codex_task(decision.text)
            return
        self._say("我在这里。这个 MVP2 会优先帮你把工程任务交给 Codex 来做。", "うん、ここにいるよ。作業したい内容をそのまま言ってね。", sprite_id="5")

    def _start_codex_task(self, goal: str) -> None:
        self._busy = True
        self._sync_input()
        job = CodexJob(self._runner, goal)
        job.signals.event.connect(self._handle_event)
        job.signals.finished.connect(self._codex_finished)
        self._pool.start(job)

    def _handle_event(self, event: AppEvent) -> None:
        self._say_event(event)
        if event.is_task_event:
            self._update_task_card(event)
        self._append_history(self._config.primary_character.name, event.display_text)

    def _codex_finished(self) -> None:
        self._busy = False
        self._sync_input()

    def _say_event(self, event: AppEvent) -> None:
        sprite = "3"
        if event.status == TaskStatus.COMPLETED:
            sprite = "5"
        elif event.status == TaskStatus.FAILED:
            sprite = "4"
        self._say(event.display_text, event.voice_text, sprite)

    def _say(self, display_text: str, voice_text: str = "", sprite_id: str = "1") -> None:
        self.stage.set_character(self._config.primary_character, sprite_id)
        self.name_label.setText(self._config.primary_character.name)
        self.name_label.setStyleSheet(f"color: {self._config.primary_character.color}; background: transparent;")
        self.speech_label.setText(display_text)
        self._speak(voice_text or display_text, sprite_id)

    def _update_task_card(self, event: AppEvent) -> None:
        status_text = {
            TaskStatus.STARTING: "准备启动",
            TaskStatus.RUNNING: "执行中",
            TaskStatus.COMPLETED: "已完成",
            TaskStatus.FAILED: "失败",
        }.get(event.status, "未知")
        if event.elapsed_seconds is not None:
            status_text += f" · {event.elapsed_seconds:.1f}s"
        self.task_status.setText(status_text)
        self.task_goal.setText(f"目标：{event.goal}")
        self.task_details.setPlainText(event.details or event.final_message or "")
        self.task_card.show()
        self.task_card.raise_()
        self.dialog_card.raise_()
        self.input_frame.raise_()

    def _sync_input(self) -> None:
        self.input.setDisabled(self._busy)
        self.send_button.setDisabled(self._busy)

    def _append_history(self, speaker: str, text: str) -> None:
        cleaned = " ".join((text or "").split())
        if not cleaned:
            return
        self._history_rows.append({"speaker": speaker, "text": cleaned[:360]})
        self._history_rows = self._history_rows[-80:]
        self._save_history()

    def _load_history(self) -> list[dict[str, str]]:
        if not self._history_path.is_file():
            return []
        try:
            payload = json.loads(self._history_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(payload, list):
            return []
        rows: list[dict[str, str]] = []
        for row in payload[-80:]:
            if isinstance(row, dict) and row.get("speaker") and row.get("text"):
                rows.append({"speaker": str(row["speaker"]), "text": str(row["text"])[:360]})
        return rows

    def _save_history(self) -> None:
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            self._history_path.write_text(json.dumps(self._history_rows, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _create_tts_client(self) -> GptSoVitsClient | None:
        if not self._config.tts.enabled:
            return None
        if (self._config.tts.provider or "").lower() != "gpt-sovits":
            return None
        try:
            return GptSoVitsClient(self._config)
        except Exception:
            return None

    def _create_audio_player(self) -> None:
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

            self._audio_output = QAudioOutput(self)
            self._audio_output.setVolume(max(0.0, min(1.0, self._config.tts.volume)))
            self._audio_player = QMediaPlayer(self)
            self._audio_player.setAudioOutput(self._audio_output)
        except Exception:
            self._audio_output = None
            self._audio_player = None

    def _speak(self, voice_text_value: str, sprite_id: str) -> None:
        text = self._tts_text(voice_text_value)
        if not text or not self._config.tts.enabled or self._tts_client is None:
            return
        self._tts_generation_id += 1
        generation_id = self._tts_generation_id
        job = TtsJob(generation_id, self._tts_client, text, self._config.primary_character, sprite_id)
        job.signals.finished.connect(self._on_tts_finished)
        job.signals.failed.connect(self._on_tts_failed)
        self._pool.start(job)

    def _tts_text(self, text: str) -> str:
        text = " ".join((text or "").split())
        if not text:
            return ""
        blocked_fragments = ("{", "}", "task_id", "codex2-", "--", "data/app2", ".jsonl", ".log")
        if any(fragment in text for fragment in blocked_fragments):
            return ""
        return text[:120]

    def _on_tts_finished(self, generation_id: int, audio_path: str) -> None:
        if generation_id != self._tts_generation_id or self._audio_player is None:
            return
        try:
            self._audio_player.stop()
            self._audio_player.setSource(QUrl.fromLocalFile(audio_path))
            self._audio_player.play()
        except Exception:
            pass

    def _on_tts_failed(self, generation_id: int, message: str) -> None:
        if generation_id == self._tts_generation_id and self._config.tts.fallback_to_system:
            self._system_tts_fallback()

    def _system_tts_fallback(self) -> None:
        try:
            from PySide6.QtTextToSpeech import QTextToSpeech
        except Exception:
            return
        tts = QTextToSpeech(self)
        tts.setVolume(max(0.0, min(1.0, self._config.tts.volume)))
        tts.say(self.speech_label.text())

    def closeEvent(self, event) -> None:
        if self._tts_client is not None:
            self._tts_client.shutdown()
        super().closeEvent(event)


def run_app(config: AppConfig) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Shinsekai MVP2")
    window = Mvp2Window(config)
    window.show()
    return app.exec()

