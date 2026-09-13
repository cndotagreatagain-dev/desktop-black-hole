"""Arrange actual framebuffer captures, without retouching their contents."""
from pathlib import Path
import json
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]/'artifacts'/'inflow_companion'


def composite(path, light=False):
    source=Image.open(path).convert('RGBA')
    background=Image.new('RGBA',source.size,'white' if light else '#17191e')
    return Image.alpha_composite(background,source).convert('RGB')


def main():
    flow=sorted((ROOT/'flow_final').glob('frame_*.png'))
    frames=[]
    metadata=json.loads((ROOT/'flow_final'/'capture.json').read_text())
    x,y=metadata['poses'][0]
    y=metadata['size'][1]-y
    crop=(round(x)-32,round(y)-45,round(x)+152,round(y)+115)
    for path in flow:
        board=Image.new('RGB',(968,428),'#17191e')
        board.paste(composite(path),(0,28))
        board.paste(composite(path).crop(crop).resize((368,320)),(600,70))
        draw=ImageDraw.Draw(board)
        draw.text((12,8),'ACTUAL GL | fixed native arrow pose | inward-moving light',fill='white')
        draw.text((612,45),'2x detail',fill='white')
        frames.append(board)
    frames[0].save(ROOT/'inward_flow.gif',save_all=True,append_images=frames[1:],duration=120,loop=0)
    frames[0].save(ROOT/'inward_flow.png')
    frames=[]
    for idle,busy in zip(sorted((ROOT/'orbit_idle_final').glob('frame_*.png')),
                         sorted((ROOT/'orbit_output_final').glob('frame_*.png'))):
        board=Image.new('RGB',(1200,428),'#17191e')
        board.paste(composite(idle),(0,28))
        board.paste(composite(busy),(600,28))
        draw=ImageDraw.Draw(board)
        draw.text((12,8),'OUTER / SLOW  |  real-time orbital speed',fill='white')
        draw.text((612,8),'INNER / FAST  |  same elapsed seconds',fill='white')
        frames.append(board)
    frames[0].save(ROOT/'companion_orbits.gif',save_all=True,append_images=frames[1:],duration=120,loop=0)
    contact=Image.new('RGB',(1200,856),'#17191e')
    for j,index in enumerate((0,4,8,12)):
        contact.paste(composite(ROOT/'cursor_final'/f'frame_{index:03d}.png',j in (0,3)),
                      ((j%2)*600,(j//2)*428+28))
    draw=ImageDraw.Draw(contact)
    draw.text((12,8),'Actual native-arrow GL poses / light and dark desktop composites',fill='white')
    contact.save(ROOT/'light_dark_contact.png')


if __name__=='__main__':main()
