import argparse
from io import BytesIO
import json
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
EMOJI_ID = re.compile(r"[a-z0-9_]+")
FIELDS = {
    "aliases", "customTriggers", "height", "ascent", "listed",
    "textReplacement", "messageReplacement", "spriteId", "category",
}


def string_list(value, field):
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{field} must be a list of nonempty strings")
    if len({item.casefold() for item in value}) != len(value):
        raise ValueError(f"{field} contains duplicate entries")
    return value


def load_metadata(path):
    metadata = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or metadata.keys() - FIELDS:
        raise ValueError("Expected an emoji object with documented fields")

    aliases = string_list(metadata.get("aliases", []), "aliases")
    if any(not EMOJI_ID.fullmatch(alias) for alias in aliases) or path.stem in aliases:
        raise ValueError("Extra aliases must be valid IDs and differ from the file name")
    string_list(metadata.get("customTriggers", []), "customTriggers")

    height = metadata.get("height", 8)
    ascent = metadata.get("ascent", 7)
    if type(height) is not int or type(ascent) is not int or not 0 <= ascent <= height or height == 0:
        raise ValueError("Expected a positive height and an ascent between zero and height")
    if type(metadata.get("listed", True)) is not bool:
        raise ValueError("listed must be a boolean")
    if "spriteId" in metadata:
        sprite = metadata["spriteId"]
        if not isinstance(sprite, str) or not EMOJI_ID.fullmatch(sprite):
            raise ValueError("spriteId must be a valid ID")

    for field in ("textReplacement", "messageReplacement"):
        if field not in metadata:
            continue
        value = metadata[field]
        if not isinstance(value, str) or not value.strip() or not value.isprintable():
            raise ValueError(f"{field} must be a nonempty line of text")
    if "§" in metadata.get("messageReplacement", ""):
        raise ValueError("messageReplacement must be plain chat text")
    return metadata


def load_png(path):
    data = path.read_bytes()
    with Image.open(BytesIO(data), formats=("PNG",)) as image:
        if image.is_animated:
            raise ValueError("Expected a static PNG")
        image.verify()
    with Image.open(BytesIO(data), formats=("PNG",)) as image:
        image.load()
    return data


def collect_emojis(directory):
    sources = sorted({
        path.with_suffix("") for path in directory.rglob("*")
        if path.is_file() and path.suffix in {".png", ".json"}
    })
    entries = {}
    textures = {}
    sprites = set()
    triggers = {}
    for base in sources:
        emoji_id = base.name
        metadata_path = base.with_suffix(".json")
        texture_path = base.with_suffix(".png")
        try:
            if not EMOJI_ID.fullmatch(emoji_id):
                raise ValueError("File names must use lowercase letters, digits and underscores")
            if emoji_id in entries:
                raise ValueError(f"Duplicate emoji ID {emoji_id!r}")
            metadata = load_metadata(metadata_path) if metadata_path.is_file() else {}
            folders = base.relative_to(directory).parts[:-1]
            category = metadata.get("category", folders[0] if folders else "general")
            if not isinstance(category, str) or not EMOJI_ID.fullmatch(category):
                raise ValueError("Category must use lowercase letters, digits and underscores")
            metadata["category"] = category
            if texture_path.is_file():
                sprite = metadata.get("spriteId", emoji_id)
                if sprite in sprites:
                    raise ValueError(f"Duplicate sprite ID {sprite!r}")
                sprites.add(sprite)
                archive_path = f"textures/{emoji_id}.png"
                textures[archive_path] = load_png(texture_path)
                metadata["texture"] = archive_path
            elif "spriteId" in metadata or not {"textReplacement", "messageReplacement"} & metadata.keys():
                raise ValueError("Expected a PNG or a text/message replacement")

            entry_triggers = [f":{alias}:" for alias in [emoji_id, *metadata.get("aliases", [])]]
            entry_triggers.extend(metadata.get("customTriggers", []))
            if "textReplacement" in metadata and "messageReplacement" in metadata:
                entry_triggers.append(metadata["messageReplacement"])
            for trigger in entry_triggers:
                owner = triggers.setdefault(trigger.casefold(), emoji_id)
                if owner != emoji_id:
                    raise ValueError(f"Trigger {trigger!r} is already used by {owner!r}")
            entries[emoji_id] = metadata
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
            raise ValueError(f"{base.relative_to(directory)}: {error}") from error
    if not entries:
        raise ValueError(f"No emojis found in {directory}")
    return entries, textures


def main():
    parser = argparse.ArgumentParser(description="Build the RFU emoji catalog and texture ZIP.")
    parser.add_argument("--version", default="local", help="Version written to the manifest")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/emojis.zip")
    args = parser.parse_args()
    try:
        entries, textures = collect_emojis(ROOT / "emojis")
    except ValueError as error:
        parser.exit(1, f"{error}\n")

    manifest = {"schemaVersion": 1, "version": args.version, "emojis": entries}
    archive_files = {
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        "NOTICE.md": (ROOT / "NOTICE.md").read_text(encoding="utf-8"),
        "LICENSE.md": (ROOT / "LICENSE.md").read_text(encoding="utf-8"),
        **textures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.output, "w") as archive:
        for name, content in archive_files.items():
            archive.writestr(ZipInfo(name), content, compress_type=ZIP_DEFLATED)
    print(f"Built {args.output} with {len(entries)} entries and {len(textures)} textures.")


if __name__ == "__main__":
    main()
