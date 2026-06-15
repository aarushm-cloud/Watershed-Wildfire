# Marks `src/` as a regular package so `from src.config import ...` /
# `from src.grids import ...` resolve to THIS directory deterministically
# (a regular package found on sys.path[0] wins over the unrelated `src`
# namespace portion that a site-packages dependency happens to ship).
# The repo root is placed on sys.path by validation/gate.py's own bootstrap
# (keyed off __file__), so this works under pytest, the standalone lock
# runner, and `python validation/gate.py` alike. See P1.1.
