"""Arrange actual production frames for before/after visual review."""
import argparse
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw


def composite(path, color):
    source = Image.open(path).convert("RGBA")
    background = Image.new("RGBA", source.size, (*color, 255))
    background.alpha_composite(source)
    return background.convert("RGB")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--background", default="#0c1016")
    parser.add_argument("--before-label", default="BEFORE - accepted baseline")
    parser.add_argument("--after-label", default="REFINED - orbital flow + platinum inner ring")
    parser.add_argument("--frame-ms", type=int, default=120)
    args = parser.parse_args()
    background = ImageColor.getrgb(args.background)
    before = sorted(args.before.glob("frame_*.png"))
    after = sorted(args.after.glob("frame_*.png"))
    if not before or len(before) != len(after):
        raise ValueError("Before/after sequences must contain the same nonzero frame count")
    pairs, refined = [], []
    for old_path, new_path in zip(before, after):
        old, new = composite(old_path, background), composite(new_path, background)
        if old.size != new.size:
            raise ValueError("The production captures must have matching dimensions")
        width, height = new.size
        board = Image.new("RGB", (width * 2, height + 32), (12, 16, 22))
        board.paste(old, (0, 32))
        board.paste(new, (width, 32))
        draw = ImageDraw.Draw(board)
        draw.text((16, 10), args.before_label, fill=(220, 224, 230))
        draw.text((width + 16, 10), args.after_label, fill=(220, 224, 230))
        pairs.append(board)
        refined.append(new)
    args.output.mkdir(parents=True, exist_ok=True)
    pairs[0].save(args.output / "before_after.png")
    pairs[0].save(args.output / "before_after.gif", save_all=True,
                  append_images=pairs[1:], duration=max(1, args.frame_ms), loop=0)
    refined[0].save(args.output / "flow_motion.gif", save_all=True,
                    append_images=refined[1:], duration=max(1, args.frame_ms), loop=0)
    contact = Image.new("RGB", (refined[0].width * 2, refined[0].height * 2))
    for cell, index in enumerate((0, len(refined) // 4, len(refined) // 2, len(refined) * 3 // 4)):
        contact.paste(refined[index],
                      ((cell % 2) * refined[0].width, (cell // 2) * refined[0].height))
    contact.save(args.output / "motion_contact.png")


if __name__ == "__main__":
    main()
