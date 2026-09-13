"""Arrange actual companion GL captures without retouching scene content."""
import argparse
from pathlib import Path
from PIL import Image, ImageDraw


def composite(path, light=False):
    source = Image.open(path).convert('RGBA')
    background = Image.new('RGBA', source.size, 'white' if light else '#17191e')
    return Image.alpha_composite(background, source).convert('RGB')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    frames = []
    paths = sorted((args.root/'before_idle').glob('frame_*.png'))
    for before, after in zip(paths, sorted((args.root/'after_idle').glob('frame_*.png'))):
        board = Image.new('RGB', (1200, 428), '#17191e')
        board.paste(composite(before), (0, 28))
        board.paste(composite(after), (600, 28))
        draw = ImageDraw.Draw(board)
        draw.text((12, 8), 'BEFORE | original direction, radius 0.17', fill='white')
        draw.text((612, 8), 'AFTER | reverse direction, radius 0.255 (+50%)', fill='white')
        frames.append(board)
    if not frames:
        raise RuntimeError('No paired GL frames found')
    # A GIF's 256-color palette can turn this tiny blue object gray because the
    # much larger gold disk dominates the quantizer. Preserve source colors.
    frames[0].save(args.root/'before_after.webp', save_all=True,
                   append_images=frames[1:], duration=120, loop=0,
                   lossless=True, method=3)
    # Four instants show continuous travel and front/rear depth. Light and dark
    # backgrounds are plain alpha composites, never sources for the shader.
    board = Image.new('RGB', (1200, 856), '#17191e')
    for j, index in enumerate((0, 24, 48, 72)):
        x, y = (j % 2)*600, (j//2)*428
        board.paste(composite(args.root/'after_busy'/f'frame_{index:03d}.png',
                              j % 2 == 1), (x, y+28))
        ImageDraw.Draw(board).text((x+12, y+8),
            f'AFTER | processing orbit | t = {index*0.12:.2f}s', fill='white')
    board.save(args.root/'light_dark.png')


if __name__ == '__main__':
    main()
