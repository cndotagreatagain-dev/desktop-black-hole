"""Capture the real geodesic companion at controlled orbital phases."""
import argparse
import json
import math
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer,Qt
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication
from desktop_black_hole import BlackHoleGLWidget,configure_surface_format
from companion import ORBIT_DIRECTION

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("output",type=Path)
    parser.add_argument("--width",type=int,default=600)
    parser.add_argument("--frames",type=int,default=32)
    parser.add_argument("--activity",choices=("idle","busy","output","unknown"),default="idle")
    parser.add_argument("--phase",type=float,default=0.45)
    parser.add_argument("--step-seconds",type=float,default=0.0)
    args=parser.parse_args()
    QApplication.setAttribute(Qt.AA_UseDesktopOpenGL)
    QSurfaceFormat.setDefaultFormat(configure_surface_format())
    app=QApplication([])
    window=BlackHoleGLWidget()
    window.setWindowFlags(Qt.Tool|Qt.FramelessWindowHint)
    window.resize(args.width,round(args.width*2/3))
    window.set_scene_quality(1)
    window.set_companion_enabled(True)
    window.set_companion_activity(args.activity)
    for _ in range(800):window._companion_orbit.advance(1/60)
    window._companion_orbit.phase=args.phase
    window._elapsed_timer=type("Clock",(),{"elapsed":lambda self:300000})()
    state={"index":0,"error":"","frames":[]}
    def fail(message):
        state["error"]=str(message)
        app.quit()
    def capture():
        index=state["index"]
        if args.step_seconds:
            for _ in range(4):
                window._companion_orbit.advance(args.step_seconds/4)
        else:
            window._companion_orbit.phase=(args.phase+ORBIT_DIRECTION*math.tau
                                           *index/max(1,args.frames)) % math.tau
            window._companion_orbit.clock=index*.16
        frame=window.grabFramebuffer()
        args.output.mkdir(parents=True,exist_ok=True)
        if not frame.save(str(args.output/f"frame_{index:03d}.png")):
            fail("Could not save GL companion capture")
            return
        state["frames"].append({"phase":window._companion_orbit.phase,
                                "radius":window._companion_orbit.radius,
                                "world_pose":window._companion_orbit.advance(0),
                                "activity":args.activity})
        state["step_seconds"]=args.step_seconds
        state["index"]+=1
        if state["index"]>=args.frames:app.quit()
        else:QTimer.singleShot(40,capture)
    window.fatal_error.connect(fail)
    window.show()
    QTimer.singleShot(700,capture)
    QTimer.singleShot(20000,lambda:fail("Capture timed out"))
    app.exec()
    window.close()
    if state["error"]:raise RuntimeError(state["error"])
    (args.output/"capture.json").write_text(json.dumps(state,indent=2),encoding="utf-8")

if __name__=="__main__":main()
