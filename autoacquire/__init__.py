"""Auto-acquire dNBR pathway (B4) -- bbox + dates in, raw dNBR GeoTIFF out:
scene_select -> [HUMAN APPROVAL GATE] -> dnbr_create -> acquire -> run_pipeline.

A network-boundary package outside src/. Import submodules lazily
(`from autoacquire import scene_select`) -- package import costs nothing.
"""
