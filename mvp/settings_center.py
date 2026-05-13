from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import yaml
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from mvp.config import AppConfig, load_app_config
from mvp.char_package import import_char_package
from mvp.source_layout import export_source_like_layout
from mvp.ui import ChatWindow


class SettingsCenter(QWidget):
    """Small source-project-style Settings center for the MVP."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root
        self._config_path = root / "config.yaml"
        self._config = load_app_config(self._config_path)
        self._chat_window: ChatWindow | None = None
        self._loading_values = False

        self.setWindowTitle("Shinsekai MVP 设置中心")
        self.setMinimumSize(760, 560)
        self._build_ui()
        self._load_values()
        self._apply_style()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("Shinsekai MVP 设置中心")
        title.setObjectName("title")
        subtitle = QLabel("源项目式最小闭环：API、角色、模板、data/config、聊天主窗。")
        subtitle.setObjectName("subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self._build_api_tab()
        self._build_character_tab()
        self._build_voice_tab()
        self._build_runtime_tab()

        row = QHBoxLayout()
        self.status_label = QLabel("")
        row.addWidget(self.status_label, 1)
        self.export_button = QPushButton("同步 data/config")
        self.export_button.clicked.connect(self._export_source_layout)
        self.save_button = QPushButton("保存设置")
        self.save_button.clicked.connect(self._save)
        self.launch_button = QPushButton("启动聊天")
        self.launch_button.clicked.connect(self._launch_chat)
        row.addWidget(self.export_button)
        row.addWidget(self.save_button)
        row.addWidget(self.launch_button)
        layout.addLayout(row)

    def _build_api_tab(self) -> None:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.use_mock = QCheckBox("使用本地 mock")
        self.base_url = QLineEdit()
        self.model = QLineEdit()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.fallback_mock = QCheckBox("调用失败时回退 mock")
        form.addRow("", self.use_mock)
        form.addRow("Base URL", self.base_url)
        form.addRow("模型", self.model)
        form.addRow("API Key", self.api_key)
        form.addRow("", self.fallback_mock)
        self.tabs.addTab(tab, "API 设定")

    def _build_character_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.active_character = QComboBox()
        self.active_character.currentIndexChanged.connect(self._active_character_changed)
        self.character_name = QLineEdit()
        self.character_setting = QTextEdit()
        self.character_setting.setMinimumHeight(260)
        self.gpt_model_path = QLineEdit()
        self.sovits_model_path = QLineEdit()
        self.refer_audio_path = QLineEdit()
        self.prompt_text = QLineEdit()
        self.prompt_lang = QLineEdit()
        self.voice_profile = QComboBox()
        self.voice_profile.currentIndexChanged.connect(self._voice_profile_changed)
        self.import_char_button = QPushButton("导入 .char 角色包")
        self.import_char_button.clicked.connect(self._import_char_package)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.addRow("当前角色", self.active_character)
        form.addRow("角色名", self.character_name)
        form.addRow("语音档位", self.voice_profile)
        form.addRow("GPT 模型 .ckpt", self.gpt_model_path)
        form.addRow("SoVITS 模型 .pth", self.sovits_model_path)
        form.addRow("参考音频", self.refer_audio_path)
        form.addRow("参考文本", self.prompt_text)
        form.addRow("参考语言", self.prompt_lang)
        layout.addLayout(form)
        layout.addWidget(self.import_char_button)
        layout.addWidget(QLabel("角色设定 / Prompt"))
        layout.addWidget(self.character_setting, 1)
        self.tabs.addTab(tab, "角色管理")

    def _build_voice_tab(self) -> None:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.tts_enabled = QCheckBox("启用 TTS")
        self.tts_provider = QLineEdit()
        self.tts_server_url = QLineEdit()
        self.tts_work_path = QLineEdit()
        self.tts_text_lang = QLineEdit()
        self.tts_prompt_lang = QLineEdit()
        self.tts_speed = QDoubleSpinBox()
        self.tts_speed.setRange(0.5, 2.0)
        self.tts_speed.setSingleStep(0.05)
        self.tts_fallback = QCheckBox("调试用：失败时回退系统 TTS")
        form.addRow("", self.tts_enabled)
        form.addRow("Provider", self.tts_provider)
        form.addRow("服务地址", self.tts_server_url)
        form.addRow("GPT-SoVITS 目录", self.tts_work_path)
        form.addRow("朗读语言", self.tts_text_lang)
        form.addRow("参考语言", self.tts_prompt_lang)
        form.addRow("语速倍率", self.tts_speed)
        form.addRow("", self.tts_fallback)
        self.tabs.addTab(tab, "语音")

    def _build_runtime_tab(self) -> None:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.show_background = QCheckBox("聊天主窗显示背景图")
        self.always_on_top = QCheckBox("聊天主窗置顶")
        self.enable_choices = QCheckBox("显示模型选项按钮")
        form.addRow("", self.show_background)
        form.addRow("", self.always_on_top)
        form.addRow("", self.enable_choices)
        self.tabs.addTab(tab, "系统 / 启动")

    def _load_values(self) -> None:
        self._loading_values = True
        c = self._config
        self.use_mock.setChecked(c.llm.use_mock)
        self.base_url.setText(c.llm.base_url)
        self.model.setText(c.llm.model)
        self.api_key.setText(c.llm.api_key)
        self.fallback_mock.setChecked(c.llm.mock_when_unconfigured)
        self._load_character_selector()
        self.character_name.setText(c.primary_character.name)
        self.character_setting.setPlainText(c.primary_character.setting)
        self._load_voice_profiles()
        self.gpt_model_path.setText(c.primary_character.voice_gpt_model_path())
        self.sovits_model_path.setText(c.primary_character.voice_sovits_model_path())
        self.refer_audio_path.setText(c.primary_character.voice_refer_audio_path())
        self.prompt_text.setText(c.primary_character.voice_prompt_text())
        self.prompt_lang.setText(c.primary_character.voice_prompt_lang(c.tts.prompt_lang))
        self.tts_enabled.setChecked(c.tts.enabled)
        self.tts_provider.setText(c.tts.provider)
        self.tts_server_url.setText(c.tts.server_url)
        self.tts_work_path.setText(c.tts.gpt_sovits_work_path)
        self.tts_text_lang.setText(c.primary_character.voice_text_lang(c.tts.text_lang))
        self.tts_prompt_lang.setText(c.primary_character.voice_prompt_lang(c.tts.prompt_lang))
        self.tts_speed.setValue(c.primary_character.voice_speech_speed(c.tts.speed_factor))
        self.tts_fallback.setChecked(c.tts.fallback_to_system)
        self.show_background.setChecked(c.app.show_background)
        self.always_on_top.setChecked(c.app.always_on_top)
        self.enable_choices.setChecked(c.app.enable_choices)
        self._loading_values = False

    def _load_character_selector(self) -> None:
        self.active_character.blockSignals(True)
        self.active_character.clear()
        for index, character in enumerate(self._config.characters):
            suffix = "（当前）" if index == 0 else ""
            self.active_character.addItem(f"{character.name}{suffix}", index)
        self.active_character.setCurrentIndex(0 if self._config.characters else -1)
        self.active_character.blockSignals(False)

    def _active_character_changed(self) -> None:
        if self._loading_values:
            return
        selected_index = self.active_character.currentData()
        if selected_index is None:
            return
        try:
            selected_index = int(selected_index)
        except (TypeError, ValueError):
            return
        if selected_index == 0:
            return

        raw = self._values_to_config_dict()
        characters = raw.get("characters")
        if not isinstance(characters, list) or selected_index >= len(characters):
            return
        selected = characters.pop(selected_index)
        characters.insert(0, selected)
        self._config_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self._config = load_app_config(self._config_path)
        self._export_source_layout(show_message=False)
        if self._chat_window is not None:
            self._chat_window.close()
            self._chat_window = None
        self._load_values()
        self.status_label.setText(f"已切换到 {self._config.primary_character.name}，并同步 data/config。")

    def _load_voice_profiles(self) -> None:
        character = self._config.primary_character
        self.voice_profile.blockSignals(True)
        self.voice_profile.clear()
        if character.voice_profiles:
            active = character.active_voice_profile or character.voice_profiles[0].id
            for profile in character.voice_profiles:
                suffix = f" / {profile.text_lang}" if profile.text_lang else ""
                self.voice_profile.addItem(f"{profile.label}{suffix}", profile.id)
            index = self.voice_profile.findData(active)
            self.voice_profile.setCurrentIndex(index if index >= 0 else 0)
        else:
            self.voice_profile.addItem("默认", "")
        self.voice_profile.blockSignals(False)

    def _voice_profile_changed(self) -> None:
        profile_id = str(self.voice_profile.currentData() or "")
        character = self._config.primary_character
        profile = next(
            (item for item in character.voice_profiles if item.id == profile_id),
            None,
        )
        if profile is None:
            return
        self.gpt_model_path.setText(profile.gpt_model_path or character.gpt_model_path)
        self.sovits_model_path.setText(profile.sovits_model_path or character.sovits_model_path)
        self.refer_audio_path.setText(profile.refer_audio_path or character.refer_audio_path)
        self.prompt_text.setText(profile.prompt_text or character.prompt_text)
        self.prompt_lang.setText(profile.prompt_lang or character.prompt_lang)
        self.tts_text_lang.setText(profile.text_lang or self._config.tts.text_lang)
        self.tts_prompt_lang.setText(profile.prompt_lang or self._config.tts.prompt_lang)
        if profile.speech_speed is not None:
            self.tts_speed.setValue(float(profile.speech_speed))

    def _values_to_config_dict(self) -> dict:
        raw = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
        raw.setdefault("app", {})
        raw["app"]["window_mode"] = "desktop_assistant"
        raw["app"]["show_background"] = self.show_background.isChecked()
        raw["app"]["always_on_top"] = self.always_on_top.isChecked()
        raw["app"]["enable_choices"] = self.enable_choices.isChecked()

        raw.setdefault("llm", {})
        raw["llm"].update(
            {
                "provider": "openai_compatible",
                "use_mock": self.use_mock.isChecked(),
                "base_url": self.base_url.text().strip(),
                "model": self.model.text().strip(),
                "api_key": self.api_key.text().strip(),
                "mock_when_unconfigured": self.fallback_mock.isChecked(),
            }
        )
        raw.setdefault("tts", {})
        raw["tts"].update(
            {
                "enabled": self.tts_enabled.isChecked(),
                "provider": self.tts_provider.text().strip() or "gpt-sovits",
                "server_url": self.tts_server_url.text().strip() or "http://127.0.0.1:9880/",
                "gpt_sovits_work_path": self.tts_work_path.text().strip(),
                "text_lang": self.tts_text_lang.text().strip() or "ja",
                "prompt_lang": self.tts_prompt_lang.text().strip() or "ja",
                "speed_factor": float(self.tts_speed.value()),
                "fallback_to_system": self.tts_fallback.isChecked(),
            }
        )

        if raw.get("characters"):
            selected_profile_id = str(self.voice_profile.currentData() or "")
            raw["characters"][0]["name"] = self.character_name.text().strip()
            raw["characters"][0]["setting"] = self.character_setting.toPlainText().strip()
            raw["characters"][0]["gpt_model_path"] = self.gpt_model_path.text().strip()
            raw["characters"][0]["sovits_model_path"] = self.sovits_model_path.text().strip()
            raw["characters"][0]["refer_audio_path"] = self.refer_audio_path.text().strip()
            raw["characters"][0]["prompt_text"] = self.prompt_text.text().strip()
            raw["characters"][0]["prompt_lang"] = self.prompt_lang.text().strip() or "ja"
            raw["characters"][0]["active_voice_profile"] = selected_profile_id
            for profile in raw["characters"][0].get("voice_profiles") or []:
                if str(profile.get("id") or "") != selected_profile_id:
                    continue
                profile.update(
                    {
                        "text_lang": self.tts_text_lang.text().strip() or "ja",
                        "prompt_lang": self.prompt_lang.text().strip() or "ja",
                        "gpt_model_path": self.gpt_model_path.text().strip(),
                        "sovits_model_path": self.sovits_model_path.text().strip(),
                        "refer_audio_path": self.refer_audio_path.text().strip(),
                        "prompt_text": self.prompt_text.text().strip(),
                        "speech_speed": float(self.tts_speed.value()),
                    }
                )
        return raw

    def _save(self) -> None:
        raw = self._values_to_config_dict()
        self._config_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self._config = load_app_config(self._config_path)
        self._export_source_layout(show_message=False)
        self._load_values()
        self.status_label.setText("已保存，并同步 data/config。")

    def _export_source_layout(self, show_message: bool = True) -> None:
        export_source_like_layout(self._config)
        if show_message:
            self.status_label.setText("已同步 data/config、角色模板、插件/MCP 占位配置。")

    def _import_char_package(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入 .char 角色包",
            "",
            "Shinsekai Character (*.char *.cha *.zip);;All Files (*)",
        )
        if not path:
            return
        try:
            summary = import_char_package(Path(path), self._config_path)
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return

        self._config = load_app_config(self._config_path)
        self._load_values()
        names = "、".join(character.name for character in summary.characters)
        self.status_label.setText(
            f"已导入 {names}，并启用 GPT-SoVITS（日语朗读、无系统中文回退）。"
        )

    def _launch_chat(self) -> None:
        self._save()
        if self._chat_window is not None:
            self._chat_window.close()
        self._chat_window = ChatWindow(self._config)
        self._chat_window.resize(900, 680)
        self._chat_window.show()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #10131a;
                color: #e5e7eb;
                font-family: Microsoft YaHei, Segoe UI, sans-serif;
                font-size: 14px;
            }
            QLabel#title {
                font-size: 22px;
                font-weight: 700;
            }
            QLabel#subtitle {
                color: #94a3b8;
            }
            QTabWidget::pane {
                border: 1px solid #263244;
                border-radius: 6px;
                background: #151a23;
            }
            QTabBar::tab {
                background: #1b2230;
                color: #cbd5e1;
                padding: 9px 18px;
            }
            QTabBar::tab:selected {
                background: #263244;
                color: #ffffff;
            }
            QLineEdit, QTextEdit {
                background: #0f1722;
                border: 1px solid #334155;
                border-radius: 5px;
                color: #f8fafc;
                padding: 7px 9px;
            }
            QPushButton {
                background: #334155;
                border: 1px solid #475569;
                border-radius: 5px;
                color: #f8fafc;
                padding: 8px 14px;
            }
            QPushButton:hover {
                background: #3f516b;
            }
            """
        )


class FirstRunDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("启动方式")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("打开设置中心，还是直接进入聊天？"))
        buttons = QDialogButtonBox()
        settings_btn = buttons.addButton("设置中心", QDialogButtonBox.ButtonRole.AcceptRole)
        chat_btn = buttons.addButton("直接聊天", QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        settings_btn.clicked.connect(lambda: self.done(1))
        chat_btn.clicked.connect(lambda: self.done(2))
        layout.addWidget(buttons)
