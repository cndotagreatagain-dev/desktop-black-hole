"""GPU timing for production scene, cursor and cached desktop lens; no OS capture."""
import argparse
import json
from pathlib import Path
from companion import CompanionOrbit
from tools.benchmark_desktop_lens import DesktopBenchmark
from tools.benchmark_black_hole_gpu import (
    OpenGLQueryBackend, build_result, load_shader_sources, measure_gpu_states,
)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    sources=load_shader_sources(Path(__file__).resolve().parents[1])
    results=[]
    for size in ((360,240),(600,400)):
        for enabled in (False,True):
            renderer=DesktopBenchmark(size,sources,'cached')
            try:
                gl=renderer.gl
                gl.glUseProgram(renderer._scene_program)
                orbit=CompanionOrbit(activity='busy')
                for _ in range(800):pose=orbit.advance(1/60)
                gl.glUniform4f(gl.glGetUniformLocation(renderer._scene_program,'u_companion'),
                              *pose[:3],pose[3] if enabled else 0)
                gl.glUniform1f(gl.glGetUniformLocation(renderer._scene_program,'u_companion_light'),pose[4])
                gl.glUniform4f(gl.glGetUniformLocation(renderer._scene_program,'u_companion_tail'),
                              *orbit.tail_uniform())
                measured=measure_gpu_states(renderer,OpenGLQueryBackend(gl),warmup=30,frames=120)
                result=build_result(size,measured,sources.cursor_shader_source)
                result['companion_enabled']=enabled
                result['desktop_mode']='cached'
                results.append(result)
                print(json.dumps(result),flush=True)
            finally:renderer.close()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(results,indent=2),encoding='utf-8')


if __name__=='__main__':main()
