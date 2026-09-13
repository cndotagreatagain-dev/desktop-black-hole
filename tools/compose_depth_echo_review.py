"""Compose actual GL captures; no retouching or generated visual assets."""
from pathlib import Path
import json
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]/'artifacts'/'depth_echo'


def composite(path, light=False):
    source = Image.open(path).convert('RGBA')
    background = Image.new('RGBA', source.size, 'white' if light else '#17191e')
    return Image.alpha_composite(background, source).convert('RGB')


def save_animation(frames, name, duration):
    frames[0].save(ROOT/name, save_all=True, append_images=frames[1:],
                   duration=duration, loop=0, optimize=True)


def main():
    frames = []
    for before, after in zip(sorted((ROOT/'orbit_before').glob('frame_*.png')),
                             sorted((ROOT/'orbit_final').glob('frame_*.png'))):
        board = Image.new('RGB', (1200, 428), '#17191e')
        board.paste(composite(before), (0, 28))
        board.paste(composite(after), (600, 28))
        draw = ImageDraw.Draw(board)
        draw.text((12,8), 'BEFORE | same orbital speed', fill='white')
        draw.text((612,8), 'AFTER | front/back depth, 31% larger sphere', fill='white')
        frames.append(board)
    save_animation(frames, 'companion_before_after.gif', 120)
    frames[0].save(ROOT/'companion_before_after.png')

    frames = []
    metadata = json.loads((ROOT/'hold_final'/'capture.json').read_text())
    x,y = metadata['poses'][0]
    y = metadata['size'][1]-y
    crop = (round(x)-28, round(y)-34, round(x)+116, round(y)+100)
    for path in sorted((ROOT/'hold_final').glob('frame_*.png')):
        board = Image.new('RGB', (960,428), '#17191e')
        frame = composite(path)
        board.paste(frame, (0,28))
        board.paste(frame.crop(crop).resize((360,335)), (600,62))
        draw = ImageDraw.Draw(board)
        draw.text((12,8), 'ACTUAL GL | held native pointer | inward contour echoes', fill='white')
        draw.text((612,40), '2.5x detail', fill='white')
        frames.append(board)
    save_animation(frames, 'cursor_echo.gif', 120)
    frames[0].save(ROOT/'cursor_echo.png')

    board = Image.new('RGB', (1200,856), '#17191e')
    draw = ImageDraw.Draw(board)
    for j,index in enumerate((0,3,6,9)):
        frame = composite(ROOT/'cursor_final'/f'frame_{index:03d}.png', j in (0,3))
        board.paste(frame, ((j%2)*600, (j//2)*428+28))
        draw.text(((j%2)*600+10, (j//2)*428+8),
                  'LIGHT BACKGROUND' if j in (0,3) else 'DARK BACKGROUND', fill='white')
    board.save(ROOT/'light_dark_contact.png')


if __name__ == '__main__':
    main()
