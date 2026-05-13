from __future__ import annotations

import traceback
from dataclasses import replace
import json
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal, Slot
from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
import yaml

from mvp.config import AppConfig, CharacterConfig, SceneConfig
from mvp.agent_runtime import AgentEvent, AgentEventType, AgentRuntime
from mvp.dialog import DialogItem, parse_dialog_response
from mvp.llm import LlmClient
from mvp.tts import GptSoVitsClient


class CharacterStage(QWidget):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self._character = config.primary_character
        self._scene = config.scene
        self._sprite_label = self._character.sprite_label("1")
        self._sprite_path = self._character.sprite_image_path("1")
        self._background_path = config.scene.background_image_path
        self._background = self._load_pixmap(self._background_path)
        self._sprite = self._load_pixmap(self._sprite_path)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_character(self, character: CharacterConfig, sprite: str) -> None:
        self._character = character
        self._sprite_label = character.sprite_label(sprite)
        new_path = character.sprite_image_path(sprite)
        if new_path != self._sprite_path:
            self._sprite_path = new_path
            self._sprite = self._load_pixmap(new_path)
        self.update()

    def set_scene(self, scene: SceneConfig) -> None:
        self._scene = scene
        new_path = scene.background_image_path
        if new_path != self._background_path:
            self._background_path = new_path
            self._background = self._load_pixmap(new_path)
        self.update()

    def _load_pixmap(self, path: str) -> QPixmap:
        if not path:
            return QPixmap()
        pix = QPixmap(str(self._config.resolve_path(path)))
        return pix

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._config.app.show_background and not self._background.isNull():
            scaled = self._background.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (scaled.width() - self.width()) // 2
            y = (scaled.height() - self.height()) // 2
            painter.drawPixmap(self.rect(), scaled, QRect(x, y, self.width(), self.height()))
            painter.fillRect(self.rect(), QColor(0, 0, 0, 54))
        elif self._config.app.show_background:
            painter.fillRect(self.rect(), QColor(self._config.app.background_color))

        if not self._sprite.isNull():
            target_h = int(self.height() * 0.84)
            target_w = int(self.width() * 0.44)
            scaled_sprite = self._sprite.scaled(
                QSize(target_w, target_h),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width() - scaled_sprite.width()) // 2
            y = max(8, self.height() - scaled_sprite.height() - int(self.height() * 0.12))
            painter.drawPixmap(x, y, scaled_sprite)
            return

        self._draw_placeholder(painter)

    def _draw_placeholder(self, painter: QPainter) -> None:

        center_x = self.width() // 2
        base_y = self.height() - 34
        body_w = min(260, max(160, self.width() // 4))
        body_h = min(330, max(220, self.height() - 80))
        color = QColor(self._character.sprite_color)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color.darker(120))
        painter.drawRoundedRect(
            center_x - body_w // 2,
            base_y - body_h,
            body_w,
            body_h,
            42,
            42,
        )
        painter.setBrush(color.lighter(135))
        head_r = body_w // 4
        painter.drawEllipse(center_x - head_r, base_y - body_h - head_r // 2, head_r * 2, head_r * 2)

        painter.setPen(QPen(QColor("#ffffff"), 2))
        painter.drawText(
            self.rect().adjusted(0, 0, 0, -12),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
            f"{self._character.name} · {self._sprite_label}",
        )


class WorkerSignals(QObject):
    finished = Signal(list, str)
    failed = Signal(str)


class ChatJob(QRunnable):
    def __init__(self, llm: LlmClient, user_text: str, image_path: Path | None = None) -> None:
        super().__init__()
        self._llm = llm
        self._user_text = user_text
        self._image_path = image_path
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            raw = (
                self._llm.chat_with_image(self._user_text, self._image_path)
                if self._image_path is not None
                else self._llm.chat(self._user_text)
            )
            self.signals.finished.emit(parse_dialog_response(raw), self._llm.last_warning)
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(str(exc))


class TtsSignals(QObject):
    finished = Signal(int, str)
    failed = Signal(int, str)


class GptSoVitsJob(QRunnable):
    def __init__(
        self,
        generation_id: int,
        client: GptSoVitsClient,
        text: str,
        character: CharacterConfig,
        sprite: str,
    ) -> None:
        super().__init__()
        self._generation_id = generation_id
        self._client = client
        self._text = text
        self._character = character
        self._sprite = sprite
        self.signals = TtsSignals()

    @Slot()
    def run(self) -> None:
        try:
            path = self._client.synthesize(self._text, self._character, self._sprite)
            self.signals.finished.emit(self._generation_id, str(path))
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(self._generation_id, str(exc))


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setModal(True)
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        model_tab = QWidget()
        model_form = QFormLayout(model_tab)
        model_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.use_mock = QCheckBox("使用本地 mock 剧情")
        self.use_mock.setChecked(config.llm.use_mock)
        self.base_url = QLineEdit(config.llm.base_url)
        self.model = QLineEdit(config.llm.model)
        self.api_key = QLineEdit(config.llm.api_key)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.mock_when_unconfigured = QCheckBox("真实模型失败或未配置时自动回退 mock")
        self.mock_when_unconfigured.setChecked(config.llm.mock_when_unconfigured)

        model_form.addRow("", self.use_mock)
        model_form.addRow("Base URL", self.base_url)
        model_form.addRow("模型", self.model)
        model_form.addRow("API Key", self.api_key)
        model_form.addRow("", self.mock_when_unconfigured)
        tabs.addTab(model_tab, "模型")

        voice_tab = QWidget()
        voice_form = QFormLayout(voice_tab)
        voice_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.tts_enabled = QCheckBox("启用系统 TTS")
        self.tts_enabled.setChecked(config.tts.enabled)
        self.tts_volume = QDoubleSpinBox()
        self.tts_volume.setRange(0.0, 1.0)
        self.tts_volume.setSingleStep(0.05)
        self.tts_volume.setValue(config.tts.volume)
        self.tts_rate = QDoubleSpinBox()
        self.tts_rate.setRange(-1.0, 1.0)
        self.tts_rate.setSingleStep(0.1)
        self.tts_rate.setValue(config.tts.rate)

        voice_form.addRow("", self.tts_enabled)
        voice_form.addRow("TTS 音量", self.tts_volume)
        voice_form.addRow("TTS 语速", self.tts_rate)
        tabs.addTab(voice_tab, "语音")

        history_tab = QWidget()
        history_layout = QVBoxLayout(history_tab)
        history_layout.setContentsMargins(18, 16, 18, 16)
        history_layout.setSpacing(12)
        history_text = QLabel("最近一次聊天会保存到 data/chat_history/latest.json，下次启动自动恢复。")
        history_text.setWordWrap(True)
        self.clear_history = QCheckBox("保存设置后清空当前历史并回到开场")
        history_layout.addWidget(history_text)
        history_layout.addWidget(self.clear_history)
        history_layout.addStretch(1)
        tabs.addTab(history_tab, "历史")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setStyleSheet(
            """
            QDialog {
                background: #f8fafc;
                color: #111827;
            }
            QLabel, QCheckBox, QTabWidget, QTabBar, QDialogButtonBox {
                color: #111827;
                background: transparent;
            }
            QLineEdit, QDoubleSpinBox {
                color: #111827;
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                padding: 6px 8px;
            }
            QTabWidget::pane {
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                background: #ffffff;
            }
            QTabBar::tab {
                background: #e5e7eb;
                color: #111827;
                padding: 8px 16px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QTabBar::tab:selected {
                background: #ffffff;
            }
            """
        )

    def values(self) -> dict:
        return {
            "llm": {
                "use_mock": self.use_mock.isChecked(),
                "base_url": self.base_url.text().strip(),
                "model": self.model.text().strip(),
                "api_key": self.api_key.text().strip(),
                "mock_when_unconfigured": self.mock_when_unconfigured.isChecked(),
            },
            "tts": {
                "enabled": self.tts_enabled.isChecked(),
                "volume": float(self.tts_volume.value()),
                "rate": float(self.tts_rate.value()),
            },
            "history": {
                "clear_history": self.clear_history.isChecked(),
            },
        }


class ChatWindow(QWidget):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self._llm = LlmClient(config)
        self._pool = QThreadPool.globalInstance()
        self._busy = False
        self._dialog_playing = False
        self._drag_origin: QPoint | None = None
        self._dialog_queue: list[DialogItem] = []
        self._current_item: DialogItem | None = None
        self._typing_text = ""
        self._typing_index = 0
        self._typing_active = False
        self._typing_timer = QTimer(self)
        self._typing_timer.setInterval(22)
        self._typing_timer.timeout.connect(self._typewriter_tick)
        self._tts = self._create_tts()
        self._tts_client = self._create_gpt_sovits_client()
        self._tts_failure_reported = False
        self._tts_generation_id = 0
        self._audio_player = None
        self._audio_output = None
        self._awaiting_tts = False
        self._audio_playing = False
        self._auto_advance_pending = False
        self._create_audio_player()
        self._agent_runtime = AgentRuntime(config.base_dir)
        self._agent_timer = QTimer(self)
        self._agent_timer.setInterval(240)
        self._agent_timer.timeout.connect(self._poll_agent_events)
        self._agent_timer.start()
        self._history_path = self._config.base_dir / "data" / "chat_history" / "latest.json"
        self._display_history_rows: list[dict[str, str]] = []
        self._last_dialog_item: DialogItem | None = None
        self._current_scene = config.scene

        self.setWindowTitle(config.app.title)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, config.app.always_on_top)
        if config.app.window_mode == "desktop_assistant":
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(760, 480)
        self._build_ui()
        self._apply_theme()
        if not self._load_history():
            self._show_intro()

    def _build_ui(self) -> None:
        self.stage = CharacterStage(self._config)
        self.stage.setParent(self)
        self.stage.installEventFilter(self)
        self.stage.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.dialog_card = QFrame()
        self.dialog_card.setParent(self)
        self.dialog_card.setObjectName("dialogCard")
        dialog_layout = QVBoxLayout(self.dialog_card)
        dialog_layout.setContentsMargins(22, 16, 22, 18)
        dialog_layout.setSpacing(6)

        self.name_label = QLabel()
        self.name_label.setObjectName("nameLabel")
        self.speech_label = QLabel()
        self.speech_label.setObjectName("speechLabel")
        self.speech_label.setWordWrap(True)
        dialog_layout.addWidget(self.name_label)
        dialog_layout.addWidget(self.speech_label)
        self.dialog_card.installEventFilter(self)
        self.name_label.installEventFilter(self)
        self.speech_label.installEventFilter(self)

        self.options_frame = QFrame(self)
        self.options_frame.setObjectName("optionsFrame")
        self.options_row = QHBoxLayout(self.options_frame)
        self.options_row.setContentsMargins(0, 0, 0, 0)
        self.options_row.setSpacing(8)
        self.options_frame.hide()

        self.input_bar = QFrame(self)
        self.input_bar.setObjectName("inputBar")
        input_row = QHBoxLayout(self.input_bar)
        input_row.setContentsMargins(10, 8, 10, 8)
        input_row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入消息")
        self.input.returnPressed.connect(self._submit_input)
        self.send_button = QPushButton("发送")
        self.send_button.clicked.connect(self._submit_input)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.send_button)

        self.history = QListWidget(self)
        self.history.setObjectName("historyList")
        self.history.hide()

        self.result_panel = QFrame(self)
        self.result_panel.setObjectName("resultPanel")
        result_layout = QVBoxLayout(self.result_panel)
        result_layout.setContentsMargins(12, 10, 12, 10)
        result_layout.setSpacing(6)
        result_header = QHBoxLayout()
        self.result_title = QLabel("工具结果")
        self.result_title.setObjectName("resultTitle")
        self.result_close = QPushButton("×")
        self.result_close.setObjectName("smallCloseButton")
        self.result_close.setToolTip("隐藏工具结果")
        self.result_close.clicked.connect(self.result_panel.hide)
        result_header.addWidget(self.result_title, 1)
        result_header.addWidget(self.result_close)
        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setObjectName("resultText")
        result_layout.addLayout(result_header)
        result_layout.addWidget(self.result_text, 1)
        self.result_panel.hide()

        self.new_chat_button = QPushButton("+", self)
        self.new_chat_button.setObjectName("roundButton")
        self.new_chat_button.setToolTip("新建对话")
        self.new_chat_button.clicked.connect(self._new_chat_requested)
        self.history_button = QPushButton("H", self)
        self.history_button.setObjectName("roundButton")
        self.history_button.setToolTip("显示或隐藏历史")
        self.history_button.clicked.connect(self._toggle_history)
        self.menu_button = QPushButton("⚙", self)
        self.menu_button.setObjectName("roundButton")
        self.menu_button.setToolTip("设置")
        self.menu_button.clicked.connect(self._open_settings)
        self.minimize_button = QPushButton("−", self)
        self.minimize_button.setObjectName("roundButton")
        self.minimize_button.setToolTip("最小化")
        self.minimize_button.clicked.connect(self.showMinimized)
        self.close_button = QPushButton("×", self)
        self.close_button.setObjectName("roundButton")
        self.close_button.setToolTip("关闭")
        self.close_button.clicked.connect(self.close)
        self.control_hint = QLabel("自动播放 · 点击/空格跳过 · H 历史 · Ctrl+N 新建 · Esc 退出", self)
        self.control_hint.setObjectName("controlHint")
        self.control_hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def _layout_overlays(self) -> None:
        w = self.width()
        h = self.height()
        self.stage.setGeometry(0, 0, w, h)
        self.stage.lower()

        button_size = 34
        top = 18
        top_buttons = [
            self.close_button,
            self.minimize_button,
            self.menu_button,
            self.history_button,
            self.new_chat_button,
        ]
        for index, button in enumerate(top_buttons):
            x_pos = w - top - button_size * (index + 1) - 8 * index
            button.setGeometry(x_pos, top, button_size, button_size)
        hint_w = min(470, max(280, w - 36))
        self.control_hint.setGeometry(w - top - hint_w, top + button_size + 8, hint_w, 24)

        if self._config.app.window_mode == "desktop_assistant":
            dialog_w = min(max(560, int(w * 0.62)), int(w * 0.86))
        else:
            dialog_w = min(max(560, int(w * 0.58)), int(w * 0.78))
        dialog_h = max(112, min(154, int(h * 0.18)))
        input_h = 52
        gap = 10
        x = (w - dialog_w) // 2
        input_y = h - input_h - 18
        dialog_y = input_y - dialog_h - gap
        self.dialog_card.setGeometry(x, dialog_y, dialog_w, dialog_h)
        self.input_bar.setGeometry(x, input_y, dialog_w, input_h)

        option_h = 42
        self.options_frame.setGeometry(x, dialog_y - option_h - 10, dialog_w, option_h)
        hist_w = min(420, max(300, int(w * 0.38)))
        self.history.setGeometry(18, 64, hist_w, max(160, h - 170))
        result_w = min(560, max(360, int(w * 0.48)))
        result_h = min(240, max(150, int(h * 0.28)))
        self.result_panel.setGeometry(18, max(70, dialog_y - result_h - 18), result_w, result_h)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_overlays()

    def _apply_theme(self) -> None:
        accent = self._config.app.accent_color
        self.setStyleSheet(
            f"""
            QWidget {{
                background: transparent;
                color: #e8edf2;
                font-family: Microsoft YaHei, Segoe UI, sans-serif;
                font-size: 15px;
            }}
            QFrame#dialogCard {{
                background: rgba(24, 25, 28, 145);
                border: 1px solid rgba(255, 255, 255, 34);
                border-radius: 4px;
            }}
            QLabel#nameLabel {{
                color: {accent};
                font-size: 14px;
                font-weight: 700;
                background: transparent;
            }}
            QLabel#speechLabel {{
                color: #f8fafc;
                font-size: 15px;
                line-height: 1.4;
                background: transparent;
            }}
            QFrame#inputBar {{
                background: rgba(250, 250, 246, 225);
                border: 1px solid rgba(255, 255, 255, 150);
                border-radius: 8px;
            }}
            QFrame#optionsFrame {{
                background: transparent;
            }}
            QLineEdit {{
                background: rgba(255, 255, 255, 225);
                color: #334155;
                border: none;
                border-radius: 6px;
                padding: 8px 10px;
                selection-background-color: {accent};
            }}
            QPushButton {{
                background: rgba(236, 147, 132, 230);
                color: #ffffff;
                border: none;
                border-radius: 15px;
                padding: 8px 14px;
            }}
            QPushButton:hover {{
                background: rgba(245, 163, 148, 240);
            }}
            QPushButton:disabled {{
                color: #f5d6d0;
                background: rgba(150, 110, 106, 190);
            }}
            QPushButton#roundButton {{
                background: rgba(48, 53, 63, 150);
                border: 1px solid rgba(255, 255, 255, 85);
                border-radius: 17px;
                color: #ffffff;
                font-size: 18px;
                padding: 0;
            }}
            QLabel#controlHint {{
                background: rgba(10, 12, 16, 100);
                color: rgba(255, 255, 255, 180);
                border-radius: 3px;
                font-size: 12px;
                padding: 2px 8px;
            }}
            QListWidget#historyList {{
                background: rgba(8, 10, 14, 165);
                border: 1px solid rgba(255, 255, 255, 40);
                border-radius: 4px;
                padding: 6px;
            }}
            QFrame#resultPanel {{
                background: rgba(8, 10, 14, 190);
                border: 1px solid rgba(255, 255, 255, 55);
                border-radius: 6px;
            }}
            QLabel#resultTitle {{
                color: #ffffff;
                font-size: 13px;
                font-weight: 700;
                background: transparent;
            }}
            QPlainTextEdit#resultText {{
                background: rgba(255, 255, 255, 230);
                color: #111827;
                border: none;
                border-radius: 4px;
                font-family: Consolas, Microsoft YaHei, monospace;
                font-size: 12px;
                padding: 8px;
            }}
            QPushButton#smallCloseButton {{
                background: rgba(255, 255, 255, 45);
                color: #ffffff;
                border-radius: 10px;
                padding: 0;
                min-width: 20px;
                max-width: 20px;
                min-height: 20px;
                max-height: 20px;
            }}
            """
        )
        self._layout_overlays()

    def _show_intro(self) -> None:
        character = self._config.primary_character
        self._display_item(
            DialogItem(
                character_name=character.name,
                speech=f"我是{character.name}。会话已经连接上了，今天先从哪个任务开始？",
                sprite="2",
            )
        )

    def _submit_input(self) -> None:
        text = self.input.text().strip()
        if not text or self._busy:
            return
        self.input.clear()
        self._clear_options()
        self._append_history("你", text)
        if text.startswith("/task "):
            task_text = text.removeprefix("/task ").strip()
            if not task_text:
                self._append_history("系统", "任务内容不能为空。")
                return
            self._agent_runtime.start_demo_task(task_text)
            return
        if text.startswith("/complete "):
            title = text.removeprefix("/complete ").strip()
            self._agent_runtime.emit_completed(title or "任务", f"{title or '任务'} 已完成。")
            return
        if text.startswith("/approve "):
            detail = text.removeprefix("/approve ").strip()
            self._agent_runtime.emit_approval_required("需要授权", detail or "这一步需要你确认授权。")
            return
        if text.startswith("/agent "):
            task_text = text.removeprefix("/agent ").strip()
            if not task_text:
                self._append_history("系统", "用法：/agent 打开浏览器搜索七海千秋评论，然后观察当前页面")
                return
            if self._agent_runtime.run_agent_request(task_text, force=True) is not None:
                return
        if self._submit_agent_tool(text):
            return
        if self._agent_runtime.run_agent_request(text) is not None:
            return
        if self._agent_runtime.run_natural_language_request(text) is not None:
            return
        if self._looks_like_browser_request(text):
            self._append_history(
                "系统",
                "这句话像浏览器操作，但当前工具路由没有命中；已阻止直接交给模型，避免角色假装浏览器已经执行。请重启聊天窗口后再试，或使用 /tool browser.search_extract 关键词。",
            )
            self._display_item(
                DialogItem(
                    character_name=self._config.primary_character.name,
                    speech="这句应该走浏览器工具，但现在没有真正调用到。我先不假装看过页面，你重启窗口后再试一次。",
                    sprite="4",
                    translate=self._voice_line(
                        "这句应该走浏览器工具，但现在没有真正调用到。我先不假装看过页面，你重启窗口后再试一次。",
                        "これはブラウザツールで実行する内容だね。今は本当に呼べていないから、見たふりはしないよ。再起動してもう一度試して。",
                    ),
                )
            )
            return
        self._set_busy(True)

        job = ChatJob(self._llm, text)
        job.signals.finished.connect(self._handle_dialog_items)
        job.signals.failed.connect(self._handle_error)
        self._pool.start(job)

    def _submit_agent_tool(self, text: str) -> bool:
        if text.startswith("/tool "):
            payload = text.removeprefix("/tool ").strip()
            tool_name, _, argument = payload.partition(" ")
            if not tool_name:
                self._append_history("系统", "用法：/tool tools；/tool search 关键词；/tool read 路径；/tool cmd 命令")
                return True
            self._agent_runtime.run_tool_request(tool_name, argument)
            return True

        shortcuts = {
            "/search ": "search",
            "/read ": "read",
            "/cmd ": "cmd",
            "/observe ": "browser.observe",
            "/codex ": "codex.submit_task",
        }
        for prefix, tool_name in shortcuts.items():
            if text.startswith(prefix):
                argument = text.removeprefix(prefix).strip()
                if not argument:
                    self._append_history("系统", f"{prefix.strip()} 后面需要参数。")
                    return True
                self._agent_runtime.run_tool_request(tool_name, argument)
                return True
        return False

    @staticmethod
    def _looks_like_browser_request(text: str) -> bool:
        lowered = text.casefold()
        browser_tokens = ("浏览器", "网页", "网站", "页面", "网上", "联网", "browser")
        action_tokens = ("搜索", "搜", "查", "找", "打开", "访问", "点击", "输入", "截图", "提取", "读取", "百科", "自搜")
        return any(token in lowered for token in browser_tokens) or (
            any(token in text for token in action_tokens)
            and any(token in text for token in ("自己", "百科", "评论", "评价", "感想", "看到什么"))
        )

    def _handle_dialog_items(self, items: list[DialogItem], warning: str = "") -> None:
        self._set_busy(False)
        if warning:
            self._append_history("系统", warning)
        normalized = [self._normalize_dialog_item(item) for item in items]
        if self._dialog_playing or self._typing_active:
            self._dialog_queue.extend(normalized)
            self._sync_input_state()
            return
        self._dialog_queue = normalized
        self._dialog_playing = bool(self._dialog_queue)
        self._sync_input_state()
        self._show_next_dialog_item()

    def _normalize_dialog_item(self, item: DialogItem) -> DialogItem:
        if item.is_scene or item.is_choice or item.is_narration:
            return item
        primary = self._config.primary_character
        sprite = item.sprite
        valid_sprites = {sprite_config.id for sprite_config in primary.sprites}
        if valid_sprites and sprite not in valid_sprites:
            sprite = primary.sprites[0].id
        if item.character_name != primary.name:
            return replace(item, character_name=primary.name, sprite=sprite)
        if sprite != item.sprite:
            return replace(item, sprite=sprite)
        return item

    def _handle_error(self, message: str) -> None:
        self._set_busy(False)
        QMessageBox.warning(self, "LLM 调用失败", message)

    def _poll_agent_events(self) -> None:
        events = self._agent_runtime.drain_events()
        if not events:
            return
        dialog_items: list[DialogItem] = []
        observation_events: list[AgentEvent] = []
        for event in events:
            readable_title = self._readable_event_title(event)
            self._append_history("任务", f"{event.type.value}: {readable_title} - {event.message}")
            detail = event.metadata.get("detail", "").strip()
            if detail:
                trimmed = detail[:900] + ("..." if len(detail) > 900 else "")
                self._append_history("工具结果", trimmed)
                if event.type in {AgentEventType.TASK_COMPLETED, AgentEventType.TASK_FAILED}:
                    self._show_tool_result(event, readable_title, detail)
            dialog_items.append(self._dialog_from_agent_event(event))
            if self._is_browser_observation_event(event):
                observation_events.append(event)
            if event.type == AgentEventType.APPROVAL_REQUIRED:
                self._show_agent_approval(event)
        if self._dialog_playing or self._typing_active:
            self._dialog_queue.extend(dialog_items)
            self._sync_input_state()
            for event in observation_events:
                self._start_browser_observation_reaction(event)
            return
        self._dialog_queue = dialog_items
        self._dialog_playing = bool(self._dialog_queue)
        self._sync_input_state()
        self._show_next_dialog_item()
        for event in observation_events:
            self._start_browser_observation_reaction(event)

    @staticmethod
    def _is_browser_observation_event(event: AgentEvent) -> bool:
        return (
            event.type == AgentEventType.TASK_COMPLETED
            and (
                event.metadata.get("tool") in {"browser.search_extract", "browser.observe"}
                or event.metadata.get("agent_observation") == "true"
            )
            and bool(event.metadata.get("detail", "").strip())
        )

    def _start_browser_observation_reaction(self, event: AgentEvent) -> None:
        detail = event.metadata.get("detail", "").strip()
        if not detail:
            return
        query = ""
        try:
            arguments = json.loads(event.metadata.get("arguments", "{}"))
            if isinstance(arguments, dict):
                query = str(arguments.get("query") or "").strip()
        except Exception:
            pass
        if not query:
            try:
                plan = json.loads(event.metadata.get("agent_plan", "[]"))
                if isinstance(plan, list):
                    for row in plan:
                        if not isinstance(row, dict):
                            continue
                        arguments = row.get("arguments")
                        if isinstance(arguments, dict) and arguments.get("query"):
                            query = str(arguments.get("query") or "").strip()
                            break
            except Exception:
                pass
        character = self._config.primary_character.name
        prompt = (
            "【浏览器观察】你刚刚看到了浏览器当前页面。\n"
            f"搜索词：{query or event.title}\n\n"
            "观察结果包含页面截图路径、视口信息、可见元素位置，以及网页可见文字节选：\n"
            f"{detail[:5200]}\n\n"
            f"请以{character}本人的第一人称，给出看到这些内容后的即时反应。"
            "如果是在搜自己、百科或别人评论，可以害羞、吐槽、轻微慌张，但不要夸张卖萌。"
            "如果内容是天气或资料查询，就简短告诉用户你看到了什么。"
            "不要提工具名、JSON、Browser Executor、搜索日志。"
        )
        image_path = self._first_artifact_path(event)
        self._append_history("浏览器观察", f"{query or event.title}：准备生成角色反应。")
        self._set_busy(True)
        job = ChatJob(self._llm, prompt, image_path=image_path)
        job.signals.finished.connect(self._handle_dialog_items)
        job.signals.failed.connect(self._handle_error)
        self._pool.start(job)

    def _first_artifact_path(self, event: AgentEvent) -> Path | None:
        artifacts = event.metadata.get("artifacts", "").splitlines()
        for artifact in artifacts:
            candidate = self._config.resolve_path(artifact.strip())
            if candidate.is_file() and candidate.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                return candidate
        return None

    def _dialog_from_agent_event(self, event: AgentEvent) -> DialogItem:
        character = self._config.primary_character
        sprite = "2"
        readable_title = self._readable_event_title(event)
        speech = event.message
        translate = self._voice_line(event.message, event.message)
        if event.type == AgentEventType.TASK_STARTED:
            speech = f"开始处理：{readable_title}。"
            translate = self._voice_line(speech, "確認しているよ。少し待ってね。")
            sprite = "3"
        elif event.type == AgentEventType.TASK_PROGRESS:
            speech = self._natural_progress_speech(event)
            translate = self._voice_line(speech, "いま確認しているよ。")
            sprite = "3"
        elif event.type == AgentEventType.APPROVAL_REQUIRED:
            speech = f"{readable_title} 需要你确认授权。"
            translate = self._voice_line(speech, "この操作には確認が必要だよ。許可してくれる？")
            sprite = "4"
        elif event.type == AgentEventType.TASK_COMPLETED:
            if event.metadata.get("tool"):
                speech = self._natural_tool_completion_speech(event)
                translate = self._voice_line(speech, self._natural_tool_completion_voice(event))
            else:
                speech = event.message
                translate = self._voice_line(speech, "完了したよ。")
            sprite = "5"
        elif event.type == AgentEventType.TASK_FAILED:
            speech = f"{readable_title} 没有跑通：{event.message}"
            translate = self._voice_line(speech, "うまくいかなかったみたい。内容を確認してね。")
            sprite = "4"
        return DialogItem(
            character_name=character.name,
            speech=speech,
            sprite=sprite,
            translate=translate,
        )

    def _show_tool_result(self, event: AgentEvent, title: str, detail: str) -> None:
        self.result_title.setText(title)
        self.result_text.setPlainText(detail)
        self.result_panel.show()
        self.result_panel.raise_()

    def _readable_event_title(self, event: AgentEvent) -> str:
        tool = event.metadata.get("tool", "")
        arguments = event.metadata.get("arguments", "")
        if not tool:
            return event.title
        if "search_text" in tool:
            return "搜索结果"
        if "read_file" in tool:
            return "文件内容"
        if "run_command" in tool:
            return "命令结果"
        if "tools.list" in tool:
            return "可用工具"
        if "mcp.list" in tool:
            return "MCP 配置"
        if "plugins.list" in tool:
            return "插件配置"
        if "events.list" in tool:
            return "事件源状态"
        if "codex." in tool or "codex.executor" in tool:
            return "Codex 任务"
        if "agent.plan" in tool:
            return "Agent 任务"
        if "browser.search_extract" in tool or "browser.observe" in tool:
            return "网页观察"
        if "browser." in tool:
            return "浏览器结果"
        return "工具结果"

    def _natural_progress_speech(self, event: AgentEvent) -> str:
        tool = event.metadata.get("tool", "")
        if "codex." in tool or "codex.executor" in tool:
            return "我已经把任务交给 Codex 这条线，正在等执行状态。"
        if "agent.plan" in tool:
            return "我在按步骤执行任务，结果马上给你。"
        if "browser." in tool:
            return "我在操作浏览器，结果马上给你。"
        if tool:
            return "我在查，结果马上给你。"
        return event.message

    def _natural_tool_completion_speech(self, event: AgentEvent) -> str:
        tool = event.metadata.get("tool", "")
        if "search_text" in tool:
            return "我找到了一些匹配内容，结果已经显示出来了。"
        if "read_file" in tool:
            return "我把文件内容打开了，结果在左侧面板里。"
        if "run_command" in tool:
            return "命令跑完了，输出已经显示出来了。"
        if "tools.list" in tool:
            return "可用工具我列出来了，你可以直接用自然语言调用。"
        if "mcp.list" in tool:
            return "MCP 配置我查到了，结果已经显示出来了。"
        if "plugins.list" in tool:
            return "插件配置我查到了，结果已经显示出来了。"
        if "events.list" in tool:
            return "事件源状态我查到了，结果已经显示出来了。"
        if "codex.submit" in tool:
            return "Codex 任务已经提交了，后续状态我会继续播报。"
        if "codex.status" in tool:
            return "Codex 任务状态我查到了，结果已经显示出来了。"
        if "codex.start" in tool:
            return "Codex 任务已经启动了，我会继续等它回写状态。"
        if "codex.cancel" in tool:
            return "Codex 取消请求已经写入了。"
        if "codex.executor" in tool:
            return "Codex 执行器回写了结果，我已经显示出来了。"
        if "agent.plan" in tool:
            if event.metadata.get("agent_observation") == "true":
                return "任务执行完了，我看到了页面内容，稍等，我整理一下自己的反应。"
            return "Agent 任务执行完了，结果已经显示出来了。"
        if "browser.search_extract" in tool or "browser.observe" in tool:
            return "我看到了浏览器画面，稍等，我整理一下自己的反应。"
        if "browser." in tool:
            return "浏览器动作执行完了，结果已经显示出来了。"
        return "工具执行完成了，结果已经显示出来了。"

    def _natural_tool_completion_voice(self, event: AgentEvent) -> str:
        tool = event.metadata.get("tool", "")
        if "search_text" in tool:
            return "一致する内容を見つけたよ。結果を表示しておいた。"
        if "read_file" in tool:
            return "ファイルの内容を開いたよ。結果を表示しておいた。"
        if "run_command" in tool:
            return "コマンドが終わったよ。出力を表示しておいた。"
        if "tools.list" in tool:
            return "使えるツールを一覧にしたよ。自然な言葉でも呼び出せる。"
        if "mcp.list" in tool:
            return "MCPの設定を確認したよ。結果を表示しておいた。"
        if "plugins.list" in tool:
            return "プラグインの設定を確認したよ。結果を表示しておいた。"
        if "events.list" in tool:
            return "イベントソースの状態を確認したよ。結果を表示しておいた。"
        if "codex.submit" in tool:
            return "Codexにタスクを渡したよ。進捗が来たら知らせるね。"
        if "codex.status" in tool:
            return "Codexの状態を確認したよ。結果を表示しておいた。"
        if "codex.start" in tool:
            return "Codexのタスクを開始したよ。進捗が来たら知らせるね。"
        if "codex.cancel" in tool:
            return "Codexへのキャンセル依頼を書き込んだよ。"
        if "codex.executor" in tool:
            return "Codexから結果が返ってきたよ。表示しておいた。"
        if "agent.plan" in tool:
            if event.metadata.get("agent_observation") == "true":
                return "タスクが終わったよ。画面の内容も見えたから、少し考えをまとめるね。"
            return "エージェントのタスクが終わったよ。結果を表示しておいた。"
        if "browser.search_extract" in tool or "browser.observe" in tool:
            return "ブラウザの画面を見たよ。少しだけ、考えをまとめるね。"
        if "browser." in tool:
            return "ブラウザ操作が終わったよ。結果を表示しておいた。"
        return "ツールの実行が終わったよ。結果を表示しておいた。"

    def _voice_line(self, chinese: str, japanese: str) -> str:
        lang = self._config.primary_character.voice_text_lang(self._config.tts.text_lang)
        lowered = (lang or "").strip().lower()
        if lowered.startswith(("zh", "cn", "yue")):
            return chinese
        if lowered.startswith(("ja", "jp")):
            return japanese
        return japanese or chinese

    def _show_agent_approval(self, event: AgentEvent) -> None:
        approval = event.approval
        detail = approval.detail if approval is not None else event.message
        answer = QMessageBox.question(
            self,
            "任务需要授权",
            f"{event.title}\n\n{detail}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        self._agent_runtime.resolve_approval(event.task_id, answer == QMessageBox.StandardButton.Yes)

    def _display_item(self, item: DialogItem) -> None:
        if item.is_scene:
            self._apply_scene_item(item)
            return
        if item.is_choice:
            self._handle_choice_item(item)
            return

        self._apply_dialog_speaker(item)
        self.speech_label.setText(item.speech)
        self._append_dialog_history(item)
        self._last_dialog_item = item
        self._save_history()

    def _show_next_dialog_item(self) -> None:
        self._typing_timer.stop()
        self._typing_active = False
        self._awaiting_tts = False
        self._audio_playing = False
        self._auto_advance_pending = False
        self._stop_tts()
        if not self._dialog_queue:
            self._current_item = None
            self._dialog_playing = False
            self._sync_input_state()
            return

        item = self._dialog_queue.pop(0)
        self._current_item = item
        if item.is_scene:
            self._apply_scene_item(item)
            self._current_item = None
            self._show_next_dialog_item()
            return
        if item.is_choice:
            self._handle_choice_item(item)
            self._current_item = None
            self._dialog_playing = False
            self._sync_input_state()
            return

        self._clear_options()
        self._apply_dialog_speaker(item)
        self._append_dialog_history(item)
        self._last_dialog_item = item
        self._save_history()
        self._start_typewriter(item.speech)
        self._speak_dialog_text(item)

    def _apply_scene_item(self, item: DialogItem) -> None:
        scene_name = item.speech.strip()
        scene = self._config.scene_by_name(scene_name)
        if scene is None:
            self._append_history("系统", f"未知场景：{scene_name}")
            return
        self._current_scene = scene
        self.stage.set_scene(scene)
        self._append_history("场景", scene.name)
        self._save_history()

    def _apply_dialog_speaker(self, item: DialogItem) -> None:
        character = self._config.character_by_name(item.character_name)
        if character is not None:
            self.stage.set_character(character, item.sprite)
            self.name_label.setText(character.name)
            self.name_label.setStyleSheet(f"color: {character.color}; background: transparent;")
            return

        self.name_label.setText("旁白")
        self.name_label.setStyleSheet("color: #f0d5a8; background: transparent;")

    def _append_dialog_history(self, item: DialogItem) -> None:
        character = self._config.character_by_name(item.character_name)
        speaker = character.name if character is not None else "旁白"
        self._append_history(speaker, item.speech)

    def _start_typewriter(self, text: str) -> None:
        self._typing_text = text or ""
        self._typing_index = 0
        self._typing_active = bool(self._typing_text)
        self.speech_label.setText("")
        if self._typing_active:
            self._typing_timer.start()
        else:
            self._finish_typewriter()

    def _typewriter_tick(self) -> None:
        step = 2 if len(self._typing_text) > 80 else 1
        self._typing_index = min(len(self._typing_text), self._typing_index + step)
        self.speech_label.setText(self._typing_text[: self._typing_index])
        if self._typing_index >= len(self._typing_text):
            self._finish_typewriter()

    def _finish_typewriter(self) -> None:
        self._typing_timer.stop()
        self._typing_active = False
        self.speech_label.setText(self._typing_text)
        if self._dialog_queue and self._dialog_queue[0].is_choice:
            QTimer.singleShot(520, self._auto_show_pending_choice)
            return
        self._maybe_auto_advance_dialog()

    def _auto_show_pending_choice(self) -> None:
        if not self._typing_active and self._dialog_queue and self._dialog_queue[0].is_choice:
            self._show_next_dialog_item()

    def _advance_dialog(self) -> bool:
        if self._typing_active:
            self._finish_typewriter()
            return True
        if self._dialog_playing:
            self._show_next_dialog_item()
            return True
        return False

    def _maybe_auto_advance_dialog(self) -> None:
        if self._typing_active or self._awaiting_tts or self._audio_playing:
            return
        if not self._dialog_playing or self._auto_advance_pending:
            return
        self._auto_advance_pending = True
        QTimer.singleShot(360, self._auto_advance_if_ready)

    def _auto_advance_if_ready(self) -> None:
        self._auto_advance_pending = False
        if self._typing_active or self._awaiting_tts or self._audio_playing:
            return
        if not self._dialog_playing:
            return
        if self._dialog_queue:
            self._show_next_dialog_item()
            return
        self._current_item = None
        self._dialog_playing = False
        self._sync_input_state()

    def _set_options(self, options: list[str]) -> None:
        self._clear_options()
        if not self._config.app.enable_choices:
            return
        for option in options:
            button = QPushButton(option)
            button.clicked.connect(lambda checked=False, value=option: self._choose_option(value))
            self.options_row.addWidget(button)
        self.options_frame.setVisible(bool(options))

    def _handle_choice_item(self, item: DialogItem) -> None:
        if self._config.app.enable_choices:
            self._append_history("选项", item.speech)
            self._set_options(item.options)
            return
        if item.options:
            self._append_history("建议", " / ".join(item.options))

    def _choose_option(self, text: str) -> None:
        self.input.setText(text)
        self._submit_input()

    def _clear_options(self) -> None:
        while self.options_row.count():
            item = self.options_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.options_frame.hide()

    def _append_history(self, speaker: str, text: str) -> None:
        item = QListWidgetItem(f"{speaker}: {text}")
        self.history.addItem(item)
        self.history.scrollToBottom()
        self._display_history_rows.append({"speaker": speaker, "text": text})
        self._save_history()

    def _load_history(self) -> bool:
        if not self._history_path.is_file():
            return False
        try:
            raw_text = self._history_path.read_text(encoding="utf-8")
            legacy_markers = (
                "剧情" + "向导",
                "剧情" + "助手",
                "游戏" + "向导",
                "新手" + "教程伙伴",
                "一起开一局游戏",
            )
            if any(term in raw_text for term in legacy_markers):
                return False
            payload = json.loads(raw_text)
        except Exception:
            return False

        rows = payload.get("display", [])
        if not isinstance(rows, list) or not rows:
            return False

        active_character = str(payload.get("active_character", "")).strip()
        last = payload.get("last_dialog", {})
        last_character = ""
        if isinstance(last, dict):
            last_character = str(last.get("character_name", "")).strip()
        primary_name = self._config.primary_character.name
        if active_character and active_character != primary_name:
            return False
        if last_character not in {"", primary_name, "NARR", "SCENE", "CHOICE"}:
            return False

        self._display_history_rows.clear()
        self.history.clear()
        for row in rows:
            speaker = str(row.get("speaker", ""))
            text = str(row.get("text", ""))
            if speaker and text:
                item = QListWidgetItem(f"{speaker}: {text}")
                self.history.addItem(item)
                self._display_history_rows.append({"speaker": speaker, "text": text})

        messages = payload.get("messages", [])
        if isinstance(messages, list):
            self._llm.import_messages(messages)

        scene_name = str(payload.get("scene", "")).strip()
        if scene_name:
            scene = self._config.scene_by_name(scene_name)
            if scene is not None:
                self._current_scene = scene
                self.stage.set_scene(scene)

        if isinstance(last, dict) and last.get("speech"):
            item = DialogItem(
                character_name=str(last.get("character_name", self._config.primary_character.name)),
                speech=str(last.get("speech", "")),
                sprite=str(last.get("sprite", "1")),
                effect=str(last.get("effect", "")),
                translate=str(last.get("translate", "")),
            )
            self._last_dialog_item = item
            self._apply_dialog_speaker(item)
            self.speech_label.setText(item.speech)
        return True

    def _save_history(self) -> None:
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            last = {}
            if self._last_dialog_item is not None:
                last = {
                    "character_name": self._last_dialog_item.character_name,
                    "speech": self._last_dialog_item.speech,
                    "sprite": self._last_dialog_item.sprite,
                    "effect": self._last_dialog_item.effect,
                    "translate": self._last_dialog_item.translate,
                }
            payload = {
                "active_character": self._config.primary_character.name,
                "messages": self._llm.export_messages(),
                "display": self._display_history_rows,
                "last_dialog": last,
                "scene": self._current_scene.name,
            }
            self._history_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.send_button.setText("等待" if busy else "发送")
        self._sync_input_state()

    def _sync_input_state(self) -> None:
        locked = self._busy or self._dialog_playing
        self.send_button.setDisabled(locked)
        self.input.setDisabled(locked)

    def _create_tts(self):
        if not self._config.tts.enabled:
            return None
        provider = (self._config.tts.provider or "system").lower()
        if provider != "system" and not self._config.tts.fallback_to_system:
            return None
        try:
            from PySide6.QtTextToSpeech import QTextToSpeech

            tts = QTextToSpeech(self)
            tts.setVolume(max(0.0, min(1.0, self._config.tts.volume)))
            tts.setRate(max(-1.0, min(1.0, self._config.tts.rate)))
            return tts
        except Exception:
            return None

    def _create_gpt_sovits_client(self) -> GptSoVitsClient | None:
        if not self._config.tts.enabled:
            return None
        if (self._config.tts.provider or "").lower() != "gpt-sovits":
            return None
        return GptSoVitsClient(self._config)

    def _create_audio_player(self) -> None:
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

            self._audio_output = QAudioOutput(self)
            self._audio_output.setVolume(max(0.0, min(1.0, self._config.tts.volume)))
            self._audio_player = QMediaPlayer(self)
            self._audio_player.setAudioOutput(self._audio_output)
            self._audio_player.mediaStatusChanged.connect(self._on_media_status_changed)
            self._audio_player.playbackStateChanged.connect(self._on_playback_state_changed)
        except Exception:
            self._audio_output = None
            self._audio_player = None

    def _speak_dialog_text(self, item: DialogItem) -> None:
        if not self._config.tts.enabled or item.is_choice:
            self._maybe_auto_advance_dialog()
            return
        character = self._config.character_by_name(item.character_name)
        text = self._tts_text_for_item(item)
        if not text:
            self._report_tts_once("当前语音语言是日语，但模型没有返回 translate 朗读稿，已跳过系统中文朗读。")
            self._maybe_auto_advance_dialog()
            return
        if self._tts_client is not None and character is not None:
            self._tts_generation_id += 1
            generation_id = self._tts_generation_id
            self._awaiting_tts = True
            job = GptSoVitsJob(generation_id, self._tts_client, text, character, item.sprite)
            job.signals.finished.connect(self._on_gpt_sovits_finished)
            job.signals.failed.connect(self._on_gpt_sovits_failed)
            self._pool.start(job)
            return
        self._speak_with_system_tts(text)
        self._maybe_auto_advance_dialog()

    def _tts_text_for_item(self, item: DialogItem) -> str:
        character = self._config.character_by_name(item.character_name)
        if character is not None:
            target_lang = character.voice_text_lang(self._config.tts.text_lang)
        else:
            target_lang = self._config.tts.text_lang
        target_lang = (target_lang or "").strip().lower()
        wants_non_chinese = target_lang and not target_lang.startswith(("zh", "cn", "yue"))
        if wants_non_chinese:
            return (item.translate or "").strip()
        return (item.speech or item.translate or "").strip()

    def _speak_with_system_tts(self, text: str) -> None:
        if self._tts is None:
            return
        try:
            self._tts.say(text)
        except Exception:
            pass

    def _on_gpt_sovits_finished(self, generation_id: int, audio_path: str) -> None:
        if generation_id != self._tts_generation_id:
            return
        self._awaiting_tts = False
        self._play_audio(audio_path)
        self._maybe_auto_advance_dialog()

    def _on_gpt_sovits_failed(self, generation_id: int, message: str) -> None:
        if generation_id != self._tts_generation_id:
            return
        self._awaiting_tts = False
        self._tts_client = None
        if self._config.tts.fallback_to_system and self._current_item is not None:
            text = self._tts_text_for_item(self._current_item)
            self._speak_with_system_tts(text)
            if message:
                self._report_tts_once(f"GPT-SoVITS 失败，已回退系统 TTS：{message}")
            self._maybe_auto_advance_dialog()
            return
        if message:
            self._report_tts_once(f"GPT-SoVITS 没有成功生成语音，已静音：{message}")
        self._maybe_auto_advance_dialog()

    def _report_tts_once(self, message: str) -> None:
        if self._tts_failure_reported:
            return
        self._tts_failure_reported = True
        self._append_history("TTS", message)

    def _play_audio(self, audio_path: str) -> None:
        if self._audio_player is None:
            self._audio_playing = False
            return
        path = (audio_path or "").strip()
        if not path:
            self._audio_playing = False
            return
        try:
            self._audio_player.stop()
            self._audio_player.setSource(QUrl.fromLocalFile(path))
            self._audio_playing = True
            self._audio_player.play()
        except Exception:
            self._audio_playing = False
            pass

    def _on_media_status_changed(self, status) -> None:
        try:
            from PySide6.QtMultimedia import QMediaPlayer

            terminal_statuses = {
                QMediaPlayer.MediaStatus.EndOfMedia,
                QMediaPlayer.MediaStatus.InvalidMedia,
            }
        except Exception:
            terminal_statuses = set()
        if status in terminal_statuses:
            self._audio_playing = False
            self._maybe_auto_advance_dialog()

    def _on_playback_state_changed(self, state) -> None:
        if not self._audio_playing:
            return
        try:
            from PySide6.QtMultimedia import QMediaPlayer

            stopped = state == QMediaPlayer.PlaybackState.StoppedState
        except Exception:
            stopped = False
        if stopped:
            self._audio_playing = False
            self._maybe_auto_advance_dialog()

    def _stop_tts(self) -> None:
        self._tts_generation_id += 1
        self._awaiting_tts = False
        self._audio_playing = False
        if self._audio_player is not None:
            try:
                self._audio_player.stop()
            except Exception:
                pass
        try:
            if self._tts is not None:
                self._tts.stop()
        except Exception:
            pass

    def _toggle_history(self) -> None:
        self.history.setVisible(not self.history.isVisible())

    def _new_chat_requested(self) -> None:
        if self._display_history_rows:
            answer = QMessageBox.question(
                self,
                "新建对话",
                "清空当前历史并回到开场？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._new_chat()

    def _new_chat(self) -> None:
        self._stop_tts()
        self._typing_timer.stop()
        self._dialog_queue.clear()
        self._current_item = None
        self._typing_text = ""
        self._typing_index = 0
        self._typing_active = False
        self._dialog_playing = False
        self._busy = False
        self._last_dialog_item = None
        self._current_scene = self._config.scene
        self._display_history_rows.clear()
        self.history.clear()
        self.input.clear()
        self._clear_options()
        self.stage.set_scene(self._current_scene)
        self._llm = LlmClient(self._config)
        self._set_busy(False)
        self._show_intro()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._config, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self._apply_settings(values)
        self._save_settings(values)
        if values.get("history", {}).get("clear_history"):
            self._new_chat()

    def _apply_settings(self, values: dict) -> None:
        llm_values = values.get("llm", {})
        tts_values = values.get("tts", {})
        object.__setattr__(
            self._config,
            "llm",
            replace(
                self._config.llm,
                use_mock=bool(llm_values.get("use_mock", self._config.llm.use_mock)),
                base_url=str(llm_values.get("base_url") or self._config.llm.base_url),
                model=str(llm_values.get("model") or self._config.llm.model),
                api_key=str(llm_values.get("api_key") or ""),
                mock_when_unconfigured=bool(
                    llm_values.get("mock_when_unconfigured", self._config.llm.mock_when_unconfigured)
                ),
            ),
        )
        object.__setattr__(
            self._config,
            "tts",
            replace(
                self._config.tts,
                enabled=bool(tts_values.get("enabled", self._config.tts.enabled)),
                volume=float(tts_values.get("volume", self._config.tts.volume)),
                rate=float(tts_values.get("rate", self._config.tts.rate)),
            ),
        )
        self._stop_tts()
        self._tts = self._create_tts()
        self._tts_client = self._create_gpt_sovits_client()
        self._tts_failure_reported = False
        self._create_audio_player()

    def _save_settings(self, values: dict) -> None:
        path = self._config.base_dir / "config.yaml"
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            raw.setdefault("llm", {}).update(values.get("llm", {}))
            raw.setdefault("tts", {}).update(values.get("tts", {}))
            path.write_text(
                yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
        except Exception as exc:
            QMessageBox.warning(self, "保存设置失败", str(exc))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            if watched in (self.stage, self.dialog_card, self.name_label, self.speech_label):
                if getattr(event, "button", lambda: None)() == Qt.MouseButton.LeftButton and self._advance_dialog():
                    return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not self.input.hasFocus() and self._advance_dialog():
                event.accept()
                return
        if event.key() == Qt.Key.Key_H and not self.input.hasFocus():
            self._toggle_history()
            event.accept()
            return
        if event.key() == Qt.Key.Key_N and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._new_chat_requested()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event) -> None:
        self._stop_tts()
        if self._tts_client is not None:
            self._tts_client.shutdown()
        super().closeEvent(event)
