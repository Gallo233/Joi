from __future__ import annotations

import base64
import json
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path

from mvp.config import AppConfig
from mvp.template import build_system_prompt


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class LlmClient:
    config: AppConfig
    messages: list[ChatMessage] = field(default_factory=list)
    last_warning: str = ""

    def __post_init__(self) -> None:
        self.messages.append(ChatMessage("system", build_system_prompt(self.config)))

    def export_messages(self) -> list[dict[str, str]]:
        return [message.__dict__.copy() for message in self.messages]

    def import_messages(self, rows: list[dict[str, str]]) -> None:
        messages = [
            ChatMessage(role=str(row.get("role", "")), content=str(row.get("content", "")))
            for row in rows
            if row.get("role") and row.get("content") is not None
        ]
        preserved = [message for message in messages if message.role != "system"]
        self.messages = [ChatMessage("system", build_system_prompt(self.config)), *preserved]

    def chat(self, user_text: str) -> str:
        self.last_warning = ""
        self.messages.append(ChatMessage("user", user_text))

        if self.config.llm.use_mock:
            response = self._mock_response(user_text)
            self.messages.append(ChatMessage("assistant", response))
            return response

        if not self.config.llm.is_configured:
            if self.config.llm.mock_when_unconfigured:
                self.last_warning = "未配置 API Key，已使用本地 mock。"
                response = self._mock_response(user_text)
                self.messages.append(ChatMessage("assistant", response))
                return response
            raise RuntimeError("LLM api_key is not configured")

        try:
            from openai import OpenAI
        except ImportError as exc:
            return self._fallback_or_raise(user_text, "openai package is not installed", exc)

        try:
            client = OpenAI(
                api_key=self.config.llm.api_key,
                base_url=self.config.llm.base_url,
            )
            response = client.chat.completions.create(
                model=self.config.llm.model,
                messages=[message.__dict__ for message in self.messages],
                temperature=self.config.llm.temperature,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or ""
            content = self._ensure_tts_translations(client, content)
            self.messages.append(ChatMessage("assistant", content))
            return content
        except Exception as exc:
            return self._fallback_or_raise(user_text, "真实模型调用失败", exc)

    def chat_with_image(self, user_text: str, image_path: Path) -> str:
        self.last_warning = ""
        if self.config.llm.use_mock or not self.config.llm.is_vision_configured or not image_path.is_file():
            if self.config.llm.vision_enabled and not self.config.llm.is_vision_configured:
                self.last_warning = "视觉模型未配置，已使用文本观察结果。"
            return self.chat(user_text)

        try:
            from openai import OpenAI
        except ImportError as exc:
            return self._fallback_or_raise(user_text, "openai package is not installed", exc)

        try:
            data_url = self._image_data_url(image_path)
            self.messages.append(ChatMessage("user", f"{user_text}\n[附带浏览器截图: {image_path.name}]"))
            client = OpenAI(
                api_key=self.config.llm.vision_api_key or self.config.llm.api_key,
                base_url=self.config.llm.vision_base_url or self.config.llm.base_url,
            )
            request_messages: list[dict] = [message.__dict__ for message in self.messages[:-1]]
            request_messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            )
            response = client.chat.completions.create(
                model=self.config.llm.vision_model or self.config.llm.model,
                messages=request_messages,
                temperature=self.config.llm.temperature,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or ""
            content = self._ensure_tts_translations(client, content)
            self.messages.append(ChatMessage("assistant", content))
            return content
        except Exception as exc:
            return self._fallback_or_raise(user_text, "视觉模型调用失败", exc)

    @staticmethod
    def _image_data_url(image_path: Path) -> str:
        mime = mimetypes.guess_type(str(image_path))[0] or "image/png"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def _ensure_tts_translations(self, client, content: str) -> str:
        if not self._needs_translation():
            return content
        payload = self._parse_dialog_payload(content)
        if payload is None:
            return content

        dialog = payload.get("dialog")
        if not isinstance(dialog, list):
            return content

        missing_indices: list[int] = []
        missing_rows: list[dict[str, str]] = []
        character_names = {character.name for character in self.config.characters}
        for index, row in enumerate(dialog):
            if not isinstance(row, dict):
                continue
            name = str(row.get("character_name", "")).strip()
            speech = str(row.get("speech", "")).strip()
            translate = str(row.get("translate", "")).strip()
            if name not in character_names or not speech or translate:
                continue
            missing_indices.append(index)
            missing_rows.append({"speech": speech})

        if not missing_rows:
            return content

        try:
            repair = client.chat.completions.create(
                model=self.config.llm.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你只负责把给出的中文台词翻成自然、简短、适合角色配音的日语。"
                            "只输出 JSON，格式必须是 {\"translations\":[\"...\"]}。"
                            "不要解释，不要改写条目数量。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(missing_rows, ensure_ascii=False),
                    },
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            repair_content = repair.choices[0].message.content or ""
            repair_payload = json.loads(repair_content)
            translations = repair_payload.get("translations")
            if not isinstance(translations, list) or len(translations) != len(missing_indices):
                return content
            for index, translated in zip(missing_indices, translations):
                text = str(translated or "").strip()
                if text:
                    dialog[index]["translate"] = text
            return json.dumps(payload, ensure_ascii=False)
        except Exception as exc:
            detail = str(exc).strip()
            self.last_warning = (
                f"模型漏掉了 translate，自动补全失败"
                f"{': ' + detail[:160] if detail else ''}"
            )
            return content

    def _needs_translation(self) -> bool:
        lang = self.config.primary_voice_text_lang().strip().lower()
        return bool(lang and not lang.startswith(("zh", "cn", "yue")))

    @staticmethod
    def _parse_dialog_payload(content: str) -> dict | None:
        try:
            payload = json.loads(content)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _fallback_or_raise(self, user_text: str, reason: str, exc: Exception) -> str:
        detail = str(exc).strip()
        message = reason if not detail else f"{reason}: {detail[:220]}"
        if self.config.llm.mock_when_unconfigured:
            self.last_warning = f"{message}。已切回本地 mock。"
            response = self._mock_response(user_text)
            self.messages.append(ChatMessage("assistant", response))
            return response
        raise RuntimeError(message) from exc

    def _mock_response(self, user_text: str) -> str:
        character = self.config.primary_character
        lowered = user_text.strip().lower()
        if not lowered or "开始" in user_text or "hello" in lowered:
            dialog = [
                {
                    "character_name": "NARR",
                    "speech": "屏幕像水面一样泛起微光，房间里的雨声忽然远了。",
                    "sprite": "1",
                    "translate": "画面が水面みたいに光って、雨の音が少し遠くなった。",
                },
                {
                    "character_name": character.name,
                    "speech": f"……嗯，连接上了。我是{character.name}。今天先从哪里开始？",
                    "sprite": "2",
                    "translate": f"……うん、つながったよ。{character.name}です。今日はどこから始める？",
                },
            ]
        elif "谁" in user_text:
            dialog = [
                {
                    "character_name": character.name,
                    "speech": f"我是{character.name}。……嗯，会陪你一起把这段任务推进完。",
                    "sprite": "2",
                    "translate": f"{character.name}です。うん、このタスクを一緒に進めるよ。",
                }
            ]
        elif "雨夜" in user_text or "电脑房" in user_text or "切换" in user_text:
            dialog = [
                {
                    "character_name": "SCENE",
                    "speech": "雨夜电脑房",
                    "sprite": "1",
                },
                {
                    "character_name": "NARR",
                    "speech": "雨声贴着玻璃滑下，电脑屏幕在黑暗里亮成一小块蓝色的湖。",
                    "sprite": "1",
                    "translate": "雨音がガラスを流れて、暗闇の中で画面だけが青く光っている。",
                },
                {
                    "character_name": character.name,
                    "speech": "这里更像调试室。要接真实模型、TTS 或生成图，大概可以从这个存档点开始。",
                    "sprite": "3",
                    "translate": "ここはデバッグルームみたい。モデルや音声、画像生成は、このセーブポイントから始められそう。",
                },
            ]
        elif "樱花" in user_text or "小径" in user_text or "回来" in user_text:
            dialog = [
                {
                    "character_name": "SCENE",
                    "speech": "樱花小径",
                    "sprite": "1",
                },
                {
                    "character_name": character.name,
                    "speech": "回到开场场景了。这里更适合做正式剧情分支……像一周目的起点。",
                    "sprite": "2",
                    "translate": "最初の場所に戻ってきたね。ここは一周目のスタート地点みたい。",
                },
            ]
        elif "观察" in user_text or "房间" in user_text:
            dialog = [
                {
                    "character_name": "NARR",
                    "speech": "石板路被夕阳染成金色，落下的花瓣像存档点一样闪着光。",
                    "sprite": "1",
                    "translate": "石畳が夕日に染まって、花びらがセーブポイントみたいに光っている。",
                },
                {
                    "character_name": character.name,
                    "speech": "如果这是游戏开场，这里应该藏着一个能改变后续分支的小物件……先记下来吧。",
                    "sprite": "3",
                    "translate": "ゲームの序盤なら、ここに分岐を変える小さなアイテムがあるかも……覚えておこう。",
                },
            ]
        elif "游戏" in user_text:
            dialog = [
                {
                    "character_name": character.name,
                    "speech": "想看游戏的话，也可以。不过先把这个原型打磨好，通关率会更高。",
                    "sprite": "4",
                    "translate": "ゲームを見るのもいいけど、先にこのプロトタイプを磨いたほうが、クリア率は上がると思う。",
                }
            ]
        else:
            dialog = [
                {
                    "character_name": character.name,
                    "speech": f"我收到的是：{user_text}。下一步可以交给真实模型扩展剧情……像开一个新分支。",
                    "sprite": "1",
                    "translate": f"受け取ったのは、{user_text}。次は本物のモデルに渡して、新しい分岐を開けそう。",
                }
            ]
        return json.dumps({"dialog": dialog}, ensure_ascii=False)
