"""Arrange unretouched GL cursor captures into a fixed-camera detail preview."""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw


def composite(path):
    source = Image.open(path).convert("RGBA")
    canvas = Image.new("RGBA", source.size, (12, 16, 22, 255))
    canvas.alpha_composite(source)
    return canvas.convert("RGB")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--crossing", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    old = sorted(args.before.glob("frame_*.png"))
    new = sorted(args.after.glob("frame_*.png"))
    if not old or len(old) != len(new):
        raise ValueError("Matching non-empty sequences are required")
    previews = []
    for before_path, after_path in zip(old, new):
        before, after = composite(before_path), composite(after_path)
        if before.size != after.size:
            raise ValueError("Capture sizes must match")
        width, height = after.size
        half = round(height * 0.30)
        crop = (width // 2 - half, height // 2 - half,
                width // 2 + half, height // 2 + half)
        board = Image.new("RGB", (half * 8, half * 4 + 28), (12, 16, 22))
        draw = ImageDraw.Draw(board)
        for index, (frame, label) in enumerate(((before, "BEFORE"), (after, "REFINED"))):
            tile = frame.crop(crop).resize((half * 4, half * 4), Image.Resampling.NEAREST)
            board.paste(tile, (index * half * 4, 28))
            draw.text((index * half * 4 + 12, 8), label + " - scripted pointer path / 2x detail",
                      fill=(220, 224, 230))
        previews.append(board)
    args.output.mkdir(parents=True, exist_ok=True)
    previews[0].save(args.output / "cursor_detail.gif", save_all=True,
                     append_images=previews[1:], duration=120, loop=0)
    previews[0].save(args.output / "cursor_detail.png")
    metadata = json.loads((args.crossing / "capture.json").read_text(encoding="utf-8"))
    indices = (0, len(metadata["poses"]) // 3, len(metadata["poses"]) * 2 // 3,
               len(metadata["poses"]) - 1)
    contact = Image.new("RGB", (660, 700), (12, 16, 22))
    draw = ImageDraw.Draw(contact)
    for cell, index in enumerate(indices):
        x, y = metadata["poses"][index]
        y = metadata["size"][1] - y
        tile = composite(args.crossing / f"frame_{index:03d}.png")
        tile = tile.crop((round(x) - 40, round(y) - 40, round(x) + 70, round(y) + 70))
        contact.paste(tile.resize((330, 330), Image.Resampling.NEAREST),
                      (cell % 2 * 330, cell // 2 * 350 + 20))
        draw.text((cell % 2 * 330 + 8, cell // 2 * 350 + 3),
                  ("OUTSIDE", "ENTERING", "NEAR CRITICAL", "INSIDE")[cell], fill="white")
    contact.save(args.output / "crossing_contact.png")


if __name__ == "__main__":
    main()
