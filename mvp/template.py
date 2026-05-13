from __future__ import annotations

from mvp.config import AppConfig


def build_system_prompt(config: AppConfig) -> str:
    primary = config.primary_character
    voice_lang = config.primary_voice_text_lang()
    character_blocks = []
    sprite_lines = "\n".join(
        f"- sprite {sprite.id}: {sprite.label}" for sprite in primary.sprites
    )
    character_blocks.append(
        "\n".join(
            [
                f"角色名: {primary.name}",
                f"角色设定: {primary.setting}",
                "可用立绘:",
                sprite_lines or "- sprite 1: 默认",
            ]
        )
    )

    scene_setting = _adapt_to_primary_character(config.scene.setting, config)
    scene_lines = "\n".join(
        f"- {scene.name}: {_adapt_to_primary_character(scene.setting, config)}" for scene in config.scenes
    )
    return f"""
你是桌面角色会话的输出层。
当前主角色: {primary.name}。
当前可说话角色: {primary.name}。
除非用户明确要求旁白或场景演出，否则只能让当前主角色本人直接回应。
不要输出其他角色名；character_name 只能是 "{primary.name}"、NARR 或 SCENE。

当前场景: {config.scene.name}
当前场景设定: {scene_setting}

可用场景:
{scene_lines or f"- {config.scene.name}: {config.scene.setting}"}

角色资料:
{chr(10).join(character_blocks)}

输出要求:
- 只输出 JSON，不要 markdown，不要解释。
- JSON 顶层格式必须是 {{"dialog": [...]}}。
- dialog 数组里的每一项必须包含 character_name、speech、sprite。
- character_name 可以是角色名，也可以是特殊值 NARR 或 SCENE。
- NARR 表示旁白，sprite 固定填 "1"。
- SCENE 表示切换背景，speech 必须填一个可用场景名，sprite 固定填 "1"。
- 角色台词必须选择合理的 sprite 编号。
- 一次回复 1 到 4 个 dialog 项，尽量短。
- 默认不要输出 CHOICE。只有用户明确要求分支选项时，才可用 CHOICE，speech 用 "/" 分隔 2 到 4 个选项。
- 不要给角色贴系统身份、服务身份、工具身份或模型身份标签。
- 如果 TTS 语音语言不是中文，必须提供 translate 字段作为朗读稿；当前朗读目标语言是 {voice_lang}。
- translate 只用于语音合成，必须自然、简短，不要写括号说明。

示例:
{{
  "dialog": [
    {{"character_name": "{primary.name}", "speech": "……嗯，连接上了。今天先从哪里开始？", "sprite": "2", "translate": "……うん、つながったよ。今日はどこから始める？"}}
  ]
}}
""".strip()


def _adapt_to_primary_character(text: str, config: AppConfig) -> str:
    adapted = text or ""
    primary_name = config.primary_character.name
    for character in config.characters:
        if character.name != primary_name:
            adapted = adapted.replace(character.name, primary_name)
    return adapted
