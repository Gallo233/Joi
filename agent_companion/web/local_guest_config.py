"""Build a local guest config that uses the providers this machine already has.

The deployed guest profile takes its providers from `/etc/joi-web.env`, which is
right for a server and wrong for trying the thing on your own laptop: the
template ships `${JOI_LLM_BASE_URL}` and `${JOI_LLM_MODEL}`, nothing on a
developer machine sets them, and the result is a Joi that starts, connects,
renders her character and then answers every message with "BYOK 请求没有成功" --
a failure that looks like a broken integration and is really an unset variable.

So the local demo reads the desktop `config.yaml` that already works and copies
its *provider selection* into the guest profile: endpoints, models, voices. Only
those. The persona, the disabled-skill list and the cost limits stay exactly as
the guest template defines them, because those are the whole point of the guest
profile and must not be inherited from a desktop that has everything switched on.

API keys are never written here. They stay as `${...}` references, which
`load_app_config` resolves from the environment and then from the system
keyring -- the same path the desktop app uses, and the reason a key does not
have to exist in any file for this to work.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml


# Non-secret provider fields worth inheriting, per config section. Anything not
# listed keeps the guest template's own value.
INHERITED_FIELDS: dict[str, tuple[str, ...]] = {
    "llm": ("provider", "base_url", "model", "temperature"),
    "tts": ("provider", "base_url", "model", "voice", "audio_format"),
    "asr": ("provider", "base_url", "model", "language"),
    "realtime_voice": ("provider", "url", "model"),
}


def _is_placeholder(value: Any) -> bool:
    text = str(value or "").strip()
    return text.startswith("${") or text.startswith("%")


def build(source: Path, template: Path) -> dict[str, Any]:
    desktop = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    guest = yaml.safe_load(template.read_text(encoding="utf-8")) or {}

    for section, fields in INHERITED_FIELDS.items():
        desktop_section = desktop.get(section)
        guest_section = guest.get(section)
        if not isinstance(desktop_section, dict) or not isinstance(guest_section, dict):
            continue
        for field in fields:
            if field not in desktop_section:
                continue
            value = desktop_section[field]
            # An unexpanded reference on the desktop side is not a setting, it
            # is the same missing variable in a different file.
            if value in (None, "") or _is_placeholder(value):
                continue
            guest_section[field] = value

    _borrow_mimo_for_asr(desktop, guest)

    # A guest Core must never fall back to canned replies: a demo that answers
    # without a provider is worse than one that says it cannot.
    llm = guest.get("llm")
    if isinstance(llm, dict):
        llm["use_mock"] = False
        llm["mock_when_unconfigured"] = False
    return guest


# MiMo serves speech in both directions from one account, so a machine set up
# for MiMo voice output can already do voice input.
MIMO_PROVIDERS = {"mimo", "xiaomi_mimo"}
MIMO_ASR_BASE_URL = "https://api.xiaomimimo.com/v1"
MIMO_ASR_MODEL = "mimo-v2.5-asr"


def _borrow_mimo_for_asr(desktop: dict[str, Any], guest: dict[str, Any]) -> None:
    """Let a MiMo voice account cover speech input as well as output.

    The guest template points ASR at a generic OpenAI-compatible endpoint, which
    needs `JOI_ASR_API_KEY` -- a variable a machine configured for MiMo has no
    reason to hold. The result is a microphone button that is present and inert.

    `AsrConfig.is_configured` wants an explicit endpoint and model even for a
    provider whose client defaults both, so they are written out here rather
    than left to the client.
    """

    guest_asr = guest.get("asr")
    desktop_tts = desktop.get("tts")
    if not isinstance(guest_asr, dict) or not isinstance(desktop_tts, dict):
        return
    if str(desktop_tts.get("provider") or "").strip().casefold() not in MIMO_PROVIDERS:
        return
    # Only step in where ASR has nothing of its own; an explicitly configured
    # speech-input provider is a deliberate choice and stays.
    if not _is_placeholder(guest_asr.get("api_key")) and guest_asr.get("api_key"):
        return
    guest_asr["provider"] = "mimo"
    guest_asr["base_url"] = MIMO_ASR_BASE_URL
    guest_asr["model"] = MIMO_ASR_MODEL
    guest_asr["api_key"] = "${JOI_ASR_API_KEY}"


def secret_exports(source: Path) -> list[str]:
    """Shell exports for keys that live in `secrets.yaml` rather than the keyring.

    `load_app_config` pulls the LLM and realtime keys from the system keyring,
    but a key kept in `secrets.yaml` beside the desktop config has no such path:
    that file sits next to `config.yaml`, and a guest workspace has neither. The
    demo hands those through the environment instead, so the value stays in
    process memory and never lands in a generated file under /tmp.
    """

    secrets_path = source.parent / "secrets.yaml"
    if not secrets_path.is_file():
        return []
    try:
        secrets = yaml.safe_load(secrets_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(secrets, dict):
        return []

    rows: list[str] = []
    for section in ("llm", "tts", "asr", "realtime_voice"):
        body = secrets.get(section)
        if not isinstance(body, dict):
            continue
        key = body.get("api_key")
        if not key or _is_placeholder(key):
            continue
        name = "JOI_QWEN_REALTIME_API_KEY" if section == "realtime_voice" else f"JOI_{section.upper()}_API_KEY"
        # Single-quoted with the standard POSIX escape, so a key containing a
        # quote cannot terminate the assignment.
        escaped = str(key).replace("'", "'\\''")
        rows.append(f"export {name}='{escaped}'")
        # One MiMo account covers speech in and out; see _borrow_mimo_for_asr.
        if section == "tts" and not _has_own_key(secrets, "asr"):
            rows.append(f"export JOI_ASR_API_KEY='{escaped}'")
    return rows


def _has_own_key(secrets: dict[str, Any], section: str) -> bool:
    body = secrets.get(section)
    if not isinstance(body, dict):
        return False
    key = body.get("api_key")
    return bool(key) and not _is_placeholder(key)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Desktop config.yaml to inherit providers from")
    parser.add_argument("--template", required=True, help="Guest profile template")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--print-env",
        action="store_true",
        help="Print shell exports for secrets.yaml keys instead of the summary; eval this.",
    )
    args = parser.parse_args(argv)

    source = Path(args.source).expanduser().resolve()
    template = Path(args.template).expanduser().resolve()
    if args.print_env:
        for row in secret_exports(source) if source.is_file() else []:
            print(row)
        return 0
    if not template.is_file():
        raise SystemExit(f"guest template does not exist: {template}")
    if not source.is_file():
        # Without a desktop config there is nothing to inherit; the template's
        # own `${...}` references still work if the environment supplies them.
        merged = yaml.safe_load(template.read_text(encoding="utf-8")) or {}
    else:
        merged = build(source, template)

    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(merged, allow_unicode=True, sort_keys=False), encoding="utf-8")

    llm = merged.get("llm") or {}
    print(f"guest config: {output}")
    print(f"  llm  : {llm.get('provider')} {llm.get('base_url')} {llm.get('model')}")
    tts = merged.get("tts") or {}
    print(f"  tts  : {'on' if tts.get('enabled') else 'off'} {tts.get('provider') or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
