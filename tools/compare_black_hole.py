"""Compose untouched production captures for visual review (requires Pillow)."""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("artifacts/reference_comparison.png"))
    args = parser.parse_args()
    capture = Image.open(args.capture).convert("RGBA")
    reference = Image.open(args.reference).convert("RGB")
    # Center the reference subject; only crop/resize, no relighting or retouching.
    reference = reference.crop((240, 70, 1840, 1030))
    reference = ImageOps.contain(reference, (600, 400), Image.Resampling.LANCZOS)
    board = Image.new("RGB", (1200, 440), (12, 16, 22))
    board.paste(reference, ((600 - reference.width) // 2, 40 + (400 - reference.height) // 2))
    dark = Image.new("RGBA", capture.size, (12, 16, 22, 255))
    dark.alpha_composite(capture)
    board.paste(dark.convert("RGB").resize((600, 400), Image.Resampling.LANCZOS), (600, 40))
    draw = ImageDraw.Draw(board)
    draw.text((20, 14), "REFERENCE (provided BMP)", fill=(210, 215, 220))
    draw.text((620, 14), "LIVE OPENGL - SCHWARZSCHILD + HDR", fill=(210, 215, 220))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    board.save(args.output)
    backgrounds = Image.new("RGB", (1200, 400))
    for x, color in ((0, (12, 16, 22)), (600, (230, 233, 239))):
        tile = Image.new("RGBA", capture.size, (*color, 255))
        tile.alpha_composite(capture)
        backgrounds.paste(tile.convert("RGB").resize((600, 400)), (x, 0))
    backgrounds.save(args.output.parent / "background_check.png")


if __name__ == "__main__":
    main()
