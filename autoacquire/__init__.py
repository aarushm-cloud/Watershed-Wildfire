"""Auto-acquire dNBR pathway (B4, AA-1/2/3) -- bbox + dates in, raw dNBR GeoTIFF out.

    scene_select.select  ->  [HUMAN APPROVAL GATE]  ->  dnbr_create.create_dnbr
        ->  acquire.build_fire_config  ->  src.pipeline.run_pipeline

A NETWORK boundary package, deliberately outside `src/` (A35 pattern): `src/` stays
a pure no-network seam. Peer of `acquire.py` / `run.py`; the created raw dNBR
converges with the upload path at the UNCHANGED `acquire.assert_raw_dnbr` /
`ingest_dnbr_both_arms` seam (A34).

Submodules are imported lazily -- `from autoacquire import scene_select` -- so that
importing the package costs nothing (no rasterio/pystac import at package import).
"""
