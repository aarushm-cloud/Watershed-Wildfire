"""Frozen fire registry for the auto-acquire dNBR stress test.

Two kinds of bbox, and the distinction matters:

  * Fires WITH a reference raster (southfork, montecito, cookspeak) take the
    reference's own footprint. A perimeter-derived box would span different
    ground than the reference, so every "agreement" number would be measuring
    the box mismatch, not the tool.
  * Fires WITHOUT a reference take the NIFC perimeter extent + a 3 km margin for
    downslope drainage (the margin convention used for the Trout DEM re-acquire).

Perimeter extents came from ArcGIS `returnExtentOnly`, NOT from walking returned
geometry: a first pass that walked geometry produced a Hermits Peak envelope
SMALLER than the fire it contains (transfer truncation).

Two data traps found while building this, recorded so nobody re-derives them:

  1. The NIFC record carrying Hermits Peak's correct ACREAGE (341,734) carries
     only the Hermits-Peak-proper GEOMETRY (~366 km2 extent, vs ~1,383 km2 of
     fire). The full combined burn geometry lives on the *Calf Canyon* record
     (~2,438 km2 extent). Selecting "the record whose acreage matches" yields a
     box covering roughly a quarter of the actual scar.
  2. "100% contained" is frequently never published. South Fork tops out at 99%
     (2024-07-15), Trout at 96% (2025-07-09), Buck at 91% (2025-06-24). Since
     post_start = containment, the operator input the post-window depends on is
     often not a knowable number. This is F-2's practical edge.

Pure data. No network, no rasters, no side effects.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_NM_DEMO = Path.home() / "Documents" / "nm-demo-dnbr"


def bbox_area_deg2(bbox) -> float:
    """Bbox area in square degrees -- the quantity acquire.MAX_BBOX_DEG2 caps."""
    west, south, east, north = bbox
    return abs(east - west) * abs(north - south)


# Fires whose bbox spans the UTM 12N/13N boundary at 108degW. All three cross on
# their RAW perimeters, not because of the 3 km margin. The 108degW boundary runs
# straight through the Gila, so this is a regional property of western New Mexico,
# not a quirk of three chosen fires. dnbr_create._zones aborts on a pair spanning
# two zones, so these are the fires where that guard may engage.
ZONE_CROSSING = frozenset({"trout", "buck", "black"})


FIRES = {
    "southfork": {
        "name": "South Fork",
        "bbox": (-105.84583, 33.28017, -105.58222, 33.45890),
        "bbox_source": "footprint of data/southfork/burn/southfork_dnbr/dnbr_native.tif "
                       "(EPSG:32613, 813x655 @ 30 m) reprojected to EPSG:4326",
        "ignition": date(2024, 6, 17),          # sfk2024-metadata.txt "Start Date"
        "containment": date(2024, 7, 15),       # 99% -- no 100% date published
        "reference": _REPO_ROOT / "data" / "southfork" / "burn" / "southfork_dnbr" / "dnbr_native.tif",
        "frozen_pair": {
            "sensor": "Landsat",
            "pre": "LC09_L2SP_033037_20240612",
            "post": "LC09_L2SP_032037_20240707",
        },
        "notes": "Frozen post scene (2024-07-07) predates containment by 8 d -- drives F-2. "
                 "Frozen pair also crosses WRS paths 033->032, which may trip the "
                 "identical-lattice guard in dnbr_create (F-3).",
    },
    "montecito": {
        "name": "Montecito / Thomas",
        "bbox": (-119.74464, 34.35202, -119.47587, 34.56438),
        "bbox_source": "footprint of validation/out/montecito_dnbr/dnbr_native.tif "
                       "(EPSG:32611, 804x765 @ 30 m) reprojected to EPSG:4326",
        "ignition": date(2017, 12, 4),
        "containment": date(2018, 1, 12),
        "reference": _REPO_ROOT / "validation" / "out" / "montecito_dnbr" / "dnbr_native.tif",
        "frozen_pair": None,
        "notes": "Pre-2022, so S2 products are below the frozen 04.00 baseline floor. "
                 "Drives F-1: the selector never filters on baseline, the creator asserts it.",
    },
    "trout": {
        "name": "Trout",
        "bbox": (-108.26739, 32.84898, -107.97605, 33.06724),
        "bbox_source": "NIFC WFIGS perimeter extent (47,294 ac, discovered 2025-06-12) "
                       "+ 3 km margin",
        "ignition": date(2025, 6, 12),
        "containment": date(2025, 7, 9),        # 96% -- no 100% date published
        "reference": None,
        "frozen_pair": None,
        "notes": "Incised Gila terrain, 12 mi N of Silver City. No local reference raster, "
                 "so selector-only. Crosses the 12N/13N boundary.",
    },
    "cookspeak": {
        "name": "Cooks Peak",
        "bbox": (-105.30104, 36.24954, -104.74906, 36.52038),
        "bbox_source": "footprint of ~/Documents/nm-demo-dnbr/dnbr_cookspeak.tif "
                       "(EPSG:32613, 2471x1500 @ 20 m) reprojected to EPSG:4326",
        "ignition": date(2022, 4, 17),
        "containment": date(2022, 5, 13),
        "reference": _NM_DEMO / "dnbr_cookspeak.tif",
        "frozen_pair": None,
        "contour_m": 1900.0,    # B2 operator input, transcribed from app.py's own
                                # documented reference value ("Cooks Peak ~1900") --
                                # range-front fire; the default 150 fails the B2 guard
                                # against this DEM's [1823, 3578] m range
        "notes": "Reference carries no provenance, so raster diff only -- no exact-pair "
                 "target. 20 m pixels imply the reference is Sentinel-2 derived.",
    },
    "hermitspeak": {
        "name": "Hermits Peak / Calf Canyon",
        "bbox": (-105.63627, 35.50551, -105.19590, 36.20497),
        "bbox_source": "NIFC perimeter extent of the CALF CANYON record (299,793 ac; the "
                       "record carrying the full combined-burn geometry) + 3 km margin. "
                       "The Hermits Peak record's geometry covers only the NE lobe.",
        "ignition": date(2022, 4, 6),
        "containment": date(2022, 8, 21),
        "reference": None,
        "frozen_pair": None,
        "notes": "341,735 ac, largest in NM history. Pre-window Jan-Apr is snow season "
                 "(SCL class 11 is in the frozen bad-mask); post-window runs into the "
                 "monsoon tail. Has a USGS post-fire debris-flow assessment.",
    },
    "cerropelado": {
        "name": "Cerro Pelado",
        "bbox": (-106.64025, 35.64550, -106.35718, 35.87919),
        "bbox_source": "NIFC perimeter extent (45,604.81 ac, exact match to the published "
                       "BAER figure) + 3 km margin",
        "ignition": date(2022, 4, 22),
        "containment": date(2022, 6, 15),
        "reference": None,
        "frozen_pair": None,
        "notes": "Only 1% high SBS -- the weak-signal case. The sharpest false-positive "
                 "test: does a barely-burned scar yield a dNBR treated as real burn?",
    },
    "buck": {
        "name": "Buck",
        "bbox": (-108.25135, 33.54317, -107.89132, 33.75667),
        "bbox_source": "NIFC WFIGS perimeter extent (57,604 ac) + 3 km margin",
        "ignition": date(2025, 6, 11),          # press: lightning ~19:30 06-11; WFIGS logs 06-12
        "containment": date(2025, 6, 24),       # 91% -- ambiguous, gets a sweep
        "reference": None,
        "frozen_pair": None,
        "notes": "Catron Co. SE of Aragon, ~77 km N of Trout -- same Gila highlands region "
                 "but NOT adjacent, so this is a monsoon-cloud test rather than a terrain "
                 "control. Containment genuinely ambiguous. Crosses the 12N/13N boundary. "
                 "Ignition taken as 06-11 (earlier of the two records) so no post-ignition "
                 "scene can leak into the pre-window.",
    },
    "eaton": {
        "name": "Eaton",
        "bbox": (-118.19465, 34.13486, -117.98054, 34.26486),
        "bbox_source": "NIFC WFIGS perimeter extent (14,021 ac) + 3 km margin",
        "ignition": date(2025, 1, 7),
        "containment": date(2025, 1, 31),
        "reference": None,
        "frozen_pair": None,
        "notes": "Altadena / San Gabriel front. Zone 11 interior. Feb-Apr 2025 post "
                 "window: LA winter, long clear stretches between storms.",
    },
    "bridge": {
        "name": "Bridge",
        "bbox": (-117.82912, 34.17166, -117.58636, 34.44896),
        "bbox_source": "NIFC WFIGS perimeter extent (54,862 ac, discovered 2024-09-08) "
                       "+ 3 km margin",
        "ignition": date(2024, 9, 8),
        "containment": date(2024, 10, 10),      # EFFECTIVE containment: CAL FIRE's active
                                                # incident updates end ~10-10; the FORMAL
                                                # declaration is 2025-06-03 -- nine months
                                                # later, the containment-date pathology at
                                                # its extreme (F-2 record). Entering the
                                                # declared date would measure winter
                                                # regrowth, not the burn.
        "reference": None,
        "frozen_pair": None,
        "notes": "San Gabriels, East Fork. Zone 11 interior; steep dissected terrain "
                 "(expect incised routing, like Post). Dry SoCal fall post window.",
    },
    "post": {
        "name": "Post",
        "bbox": (-118.90444, 34.62543, -118.76105, 34.82170),
        "bbox_source": "NIFC perimeter extent (15,563 ac vs published 15,690) + 3 km margin",
        "ignition": date(2024, 6, 15),
        "containment": date(2024, 6, 26),       # CAL FIRE published date
        "reference": None,
        "frozen_pair": None,
        "notes": "Gorman CA, zone 11 interior. Bone-dry SoCal summer post window -- "
                 "the best dual-sensor odds in the slate.",
    },
    "laguna": {
        "name": "Laguna",
        "bbox": (-106.83300, 36.24406, -106.65433, 36.41642),
        "bbox_source": "NIFC WFIGS perimeter extent (17,414 ac, discovered 2025-06-25, "
                       "matches the published 17,415) + 3 km margin",
        "ignition": date(2025, 6, 25),
        "containment": date(2025, 9, 30),       # USFS: 100% contained -- published date
        "reference": None,
        "frozen_pair": None,
        "notes": "Coyote RD, NW Santa Fe NF. Zone 13 interior. Post window opens "
                 "post-monsoon October -- usually clear NM skies.",
    },
    "mcbride": {
        "name": "McBride",
        "bbox": (-105.68455, 33.29998, -105.55118, 33.43243),
        "bbox_source": "NIFC perimeter extent (6,159 ac, exact match to the published "
                       "figure) + 3 km margin",
        "ignition": date(2022, 4, 12),
        "containment": date(2022, 5, 7),        # fully contained -- a clean date, rare
        "reference": None,
        "frozen_pair": None,
        "notes": "Ruidoso, two years before South Fork on overlapping terrain. "
                 "Dry-season post window (May-Jul 2022). Zone 13 interior.",
    },
    "salt": {
        "name": "Salt",
        "bbox": (-105.74325, 33.21432, -105.55609, 33.33434),
        "bbox_source": "NIFC perimeter extent (7,939 ac; identical across hist20/WFIGS "
                       "records) + 3 km margin",
        "ignition": date(2024, 6, 17),          # first reported ~14:20 MDT 06-17
        "containment": date(2024, 7, 15),       # 'contained July 2024' -- no exact date
                                                # published; joint South Fork incident
                                                # convention (same management, same updates)
        "reference": None,
        "frozen_pair": None,
        "notes": "Mescalero, directly S of the South Fork box on near-identical dates -- "
                 "the spec's sharpest acquisition control. Zone 13 interior.",
    },
    "putah": {
        "name": "Putah",
        "bbox": (-122.09634, 38.50111, -121.99000, 38.54961),
        "bbox_source": "out/putah_fire_2026/PROVENANCE.txt RECOMMENDED bbox (scar + ~1.5 km "
                       "buffer, extended E to the Winters valley front) -- the validated "
                       "Phase-4 live-verification fire",
        "ignition": date(2026, 6, 8),           # PROVENANCE.txt
        "containment": date(2026, 6, 20),       # the repo's putah known-answer convention
        "reference": _REPO_ROOT / "out" / "putah_fire_2026" / "dnbr_putah_2026_raw.tif",
        "frozen_pair": None,                    # hand-built S2 pair recorded in PROVENANCE.txt,
                                                # but the raster covers the wider raster-extent
                                                # box, so no exact-pair target on THIS bbox
        "notes": "860 ac, Yolo Co CA, zone 10 interior. The auto-acquire build-log fire "
                 "(creator reproduced the hand-built dNBR at r=0.9953).",
    },
    "black": {
        "name": "Black",
        "bbox": (-108.12768, 32.90868, -107.55843, 33.47680),
        "bbox_source": "NIFC perimeter extent (325,135.7 ac, matches the published 325,133 "
                       "to within 3 ac) + 3 km margin",
        "ignition": date(2022, 5, 13),
        "containment": date(2022, 7, 27),
        "reference": None,
        "frozen_pair": None,
        "notes": "Cross-UTM probe: spans 108degW. Second-largest fire in NM history.",
    },
}
