"""Development tools for the desktop black-hole project."""

from __future__ import annotations

import sys


# The benchmark can inspect another worktree.  Never let importing this package
# create bytecode in the target tree when both happen to be the same directory.
sys.dont_write_bytecode = True
