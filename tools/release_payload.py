"""Explicit publication inputs and fail-closed release-tree validation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import stat

MANIFEST = "release-files.txt"
DSH_FILES = ("index.mjs", "package.json", "README.md", "README.en.md")


def safe_file(root: Path, name: str) -> Path:
    """Reject traversal, alternate streams, symlinks and Windows junctions."""
    root = root.absolute()
    parts = PurePosixPath(name).parts
    if (not parts or name != "/".join(parts) or "\\" in name or ":" in name
            or any(part in (".", "..") for part in parts)
            or PurePosixPath(name).is_absolute()):
        raise ValueError("Invalid release path: " + name)
    path = root
    for part in (None, *parts):
        if part is not None:
            path = path / part
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Linked release input is not allowed: " + name)
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Release input is not a contained file: " + name)
    return path


def source_files(root: Path) -> list[Path]:
    manifest = safe_file(root, MANIFEST)
    names = [line.strip() for line in manifest.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    if not names or len(names) != len(set(names)):
        raise ValueError("Release manifest is empty or contains duplicate paths.")
    return [safe_file(root, name) for name in sorted(names)]


def release_files(root: Path) -> list[Path]:
    """Enumerate assembled output while refusing all linked entries."""
    files = []
    # inspect directories too: rglob must not silently hide a linked subtree.
    for path in root.rglob("*"):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Linked release output is not allowed.")
        if path.is_file():
            files.append(safe_file(root, path.relative_to(root).as_posix()))
    return sorted(files)


def verify_sealed_inputs(root: Path) -> list[Path]:
    """Do not silently absorb files added since the build was sealed."""
    manifest = safe_file(root, "SHA256SUMS.json")
    expected = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(expected, dict) or not expected:
        raise ValueError("Invalid checksum manifest.")
    paths = release_files(root)
    actual = {p.relative_to(root).as_posix() for p in paths if p != manifest}
    if actual != set(expected):
        raise ValueError("Release contents changed: unexpected or missing files.")
    for name, digest in expected.items():
        if hashlib.sha256(safe_file(root, name).read_bytes()).hexdigest() != digest:
            raise ValueError("Release content changed: " + name)
    return paths
