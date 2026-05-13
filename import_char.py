from __future__ import annotations

import argparse
from pathlib import Path

from mvp.char_package import import_char_package


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a Shinsekai .char package into the MVP.")
    parser.add_argument("package", help="Path to .char package")
    parser.add_argument("--config", default="config.yaml", help="Path to MVP config.yaml")
    args = parser.parse_args()

    summary = import_char_package(Path(args.package), Path(args.config))
    for character in summary.characters:
        print(
            f"Imported {character.name}: "
            f"{character.sprite_count} sprites, "
            f"gpt={character.gpt_model_path or '-'}, "
            f"sovits={character.sovits_model_path or '-'}, "
            f"ref={character.refer_audio_path or '-'}"
        )


if __name__ == "__main__":
    main()
