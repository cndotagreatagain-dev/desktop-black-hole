"""Compare sampling and integration independently in the production renderer."""
import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--variant", choices=("baseline", "samples", "integration", "combined"), required=True)
    args, capture_args = parser.parse_known_args()
    sys.path.insert(0, str(args.source_root.resolve()))
    import gargantua_scene_shader as scene

    source = scene.SCENE_FRAGMENT_SHADER_SOURCE

    def replace(old, new):
        nonlocal source
        if old not in source:
            raise ValueError(f"Probe does not match the selected source snapshot: {old}")
        source = source.replace(old, new)

    if args.variant in ("samples", "combined"):
        replace("int grid = u_quality < 0.5 ? 2 : (u_quality < 1.5 ? 3 : 4);", "int grid = 8;")
        replace("sampleIndex < 16", "sampleIndex < 64")
    if args.variant in ("integration", "combined"):
        replace("MAX_GEODESIC_STEPS = 420", "MAX_GEODESIC_STEPS = 840")
        replace("180 : (u_quality < 1.5 ? 280 : 420)", "360 : (u_quality < 1.5 ? 560 : 840)")
        replace("? 0.050", "? 0.025")
        replace("? 0.0375 : 0.030", "? 0.01875 : 0.015")
    scene.SCENE_FRAGMENT_SHADER_SOURCE = source
    from tools.capture_black_hole import main as capture
    sys.argv = [sys.argv[0], *capture_args]
    return capture()


if __name__ == "__main__":
    raise SystemExit(main())
