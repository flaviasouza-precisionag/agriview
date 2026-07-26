# Copyright (c) 2026 Flavia Luize Pereira de Souza.
# All rights reserved. See LICENSE for permitted portfolio-evaluation use.

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Iterable, List, Tuple

import geopandas as gpd
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import nearest_points


"""
AGRIVIEW - STEP 1 (FIELD-CENTRIC, ONE POINT PER FIELD)
------------------------------------------------------------------
OBJECTIVE
    Create exactly ONE Google Street View candidate point per field using a
    field-centric strategy.

WHY THIS VERSION EXISTS
    The adaptive version is stronger for full spatial coverage, but in some
    situations a lower-cost option is needed.

    This version keeps the same field-centric logic:
        - uses field boundaries
        - uses roads
        - identifies valid road frontage segments
        - removes unstable edges
        - filters strong curves
        - computes field-facing camera headings

    However, instead of placing multiple points along each frontage, it chooses:
        - the BEST frontage for each field
        - the CENTER point of that frontage

MAIN IDEA
    For each field:
        1. find nearby road segments
        2. extract frontage candidates
        3. trim edges and remove bad geometries
        4. keep only valid frontages
        5. choose the best frontage (longest valid frontage)
        6. place ONE point at the center of that frontage

WHY THE LONGEST FRONTAGE?
    Because it is usually the most stable and representative road segment
    facing the field.

IMPORTANT CONCEPT
    The Google Street View request point stays ON THE ROAD.

    We do NOT move the Google request point into the field.

    The field target point is only a geometric reference used to describe
    the intended field-facing direction.
"""


# ============================================================
# PROJECT PATHS
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"
OUTPUT_DIR = PROJECT_DIR / "outputs" / "field_centric_one_per_field"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# USER SETTINGS
# ============================================================

# Agricultural fields shapefile
FIELDS_SHP = DATA_DIR / "agricultural_field_boundaries.shp"

# Unique field ID column
FIELD_ID_COL = "Field_ID"

# Roads shapefile
ROADS_SHP = DATA_DIR / "road_network_lines.shp"

# Original road class column from the roads shapefile
ROADS_CLASS_COL = "fclass"

# Output folder
OUTPUT_DIR = PROJECT_DIR / "outputs" / "field_centric_one_per_field"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# ANALYSIS SETTINGS
# ============================================================

# Keep all road classes for now.
KEEP_ROAD_CLASSES = None

# CRS used for geometry calculations in meters
WORKING_CRS = "EPSG:5070"

# CRS used for final latitude/longitude export
WGS84 = "EPSG:4326"

# ------------------------------------------------------------
# FIELD-ROAD ADJACENCY DISTANCE
# ------------------------------------------------------------
# Road parts within this distance from the field can be considered as
# possible frontage candidates.
FIELD_TO_ROAD_MAX_DISTANCE_M = 30

# ------------------------------------------------------------
# MINIMUM FRONTAGE LENGTH
# ------------------------------------------------------------
# After trimming the frontage edges, the remaining line must still be at
# least this long to be considered stable and useful.
MIN_FRONTAGE_LENGTH_M = 35

# ------------------------------------------------------------
# FRONTAGE END TRIM
# ------------------------------------------------------------
# Distance removed from BOTH ends of each frontage candidate.
# This helps remove corners, unstable edges, and blind positions.
FRONTAGE_END_TRIM_M = 30

# ------------------------------------------------------------
# LOCAL ROAD BEARING WINDOW
# ------------------------------------------------------------
# Used to estimate local road direction around the point.
BEARING_SEGMENT_HALF_LENGTH_M = 10

# ------------------------------------------------------------
# INTERNAL FIELD TARGET OFFSET
# ------------------------------------------------------------
# Used only as a geometric reference point inside the field.
FIELD_TARGET_OFFSET_M = 30

# ------------------------------------------------------------
# HEADING OFFSETS
# ------------------------------------------------------------
# Central heading is field-facing. We also keep +/- 20° alternatives.
HEADING_OFFSET_DEGREES = 20

# ------------------------------------------------------------
# STRONG CURVE FILTER
# ------------------------------------------------------------
# If road direction changes more than this threshold around the point,
# the point is considered to lie on a strong curve and is discarded.
MAX_LOCAL_CURVE_DEGREES = 30

# Optional limit for quick testing
MAX_FIELDS = None

# Progress print interval
PROGRESS_EVERY = 1000


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def normalize_heading(angle: float) -> float:
    """
    Normalize any angle to the range [0, 360).
    """
    return angle % 360


def calculate_bearing(p1: Point, p2: Point) -> float:
    """
    Calculate planar compass bearing from p1 to p2.

    Compass interpretation:
        0   = North
        90  = East
        180 = South
        270 = West
    """
    dx = p2.x - p1.x
    dy = p2.y - p1.y
    angle_rad = math.atan2(dx, dy)
    angle_deg = math.degrees(angle_rad)
    return normalize_heading(angle_deg)


def angular_difference_deg(a1: float, a2: float) -> float:
    """
    Compute the smallest angular difference between two headings.
    """
    diff = abs(a1 - a2) % 360
    return min(diff, 360 - diff)


def estimate_local_bearing(line: LineString, distance_along: float, half_len: float) -> float:
    """
    Estimate local road direction near a point.
    """
    start_d = max(0.0, distance_along - half_len)
    end_d = min(line.length, distance_along + half_len)

    if math.isclose(start_d, end_d):
        start_d = max(0.0, distance_along - 1.0)
        end_d = min(line.length, distance_along + 1.0)

    p1 = line.interpolate(start_d)
    p2 = line.interpolate(end_d)
    return calculate_bearing(p1, p2)


def is_point_on_strong_curve(
    line: LineString,
    distance_along: float,
    half_len: float,
    max_curve_deg: float,
) -> Tuple[bool, float]:
    """
    Check whether a point lies on a strong curve by comparing road direction
    before and after the point.
    """
    if line.length == 0:
        return False, 0.0

    d0 = max(0.0, distance_along - half_len)
    d1 = distance_along
    d2 = min(line.length, distance_along + half_len)

    if math.isclose(d0, d1) or math.isclose(d1, d2):
        return False, 0.0

    p_before_start = line.interpolate(d0)
    p_before_end = line.interpolate(d1)
    p_after_start = line.interpolate(d1)
    p_after_end = line.interpolate(d2)

    bearing_before = calculate_bearing(p_before_start, p_before_end)
    bearing_after = calculate_bearing(p_after_start, p_after_end)

    curve_deg = angular_difference_deg(bearing_before, bearing_after)
    return curve_deg > max_curve_deg, curve_deg


def get_side_of_field(road_point: Point, road_bearing: float, field_geom) -> str:
    """
    Determine whether the field lies on the left or right side of the road.
    """
    theta = math.radians(road_bearing)
    road_dx = math.sin(theta)
    road_dy = math.cos(theta)

    road_pt, field_target_pt = nearest_points(road_point, field_geom)
    target_dx = field_target_pt.x - road_pt.x
    target_dy = field_target_pt.y - road_pt.y

    cross_z = road_dx * target_dy - road_dy * target_dx
    return "left" if cross_z > 0 else "right"


def heading_perpendicular_to_field_side(road_bearing: float, field_side: str) -> float:
    """
    Create a perpendicular heading toward the side containing the field.
    """
    if field_side == "left":
        return normalize_heading(road_bearing - 90)
    return normalize_heading(road_bearing + 90)


def move_point_along_heading(point: Point, heading_deg: float, distance_m: float) -> Point:
    """
    Move a point by a given distance in the direction of a compass heading.
    """
    theta = math.radians(heading_deg)
    dx = math.sin(theta) * distance_m
    dy = math.cos(theta) * distance_m
    return Point(point.x + dx, point.y + dy)


def compute_field_target_point(
    road_point: Point,
    field_geom,
    heading_center: float,
    offset_m: float,
) -> Point:
    """
    Create an interior reference point approximately offset_m inside the field.
    """
    candidate = move_point_along_heading(road_point, heading_center, offset_m)

    if field_geom.contains(candidate):
        return candidate

    road_pt, edge_pt = nearest_points(road_point, field_geom)
    centroid = field_geom.centroid

    mid = Point((edge_pt.x + centroid.x) / 2.0, (edge_pt.y + centroid.y) / 2.0)
    if field_geom.contains(mid):
        return mid

    return centroid


def iter_lines(geom) -> Iterable[LineString]:
    """
    Yield LineString parts from LineString or MultiLineString geometries.
    """
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, LineString):
        return [geom]
    if isinstance(geom, MultiLineString):
        return [g for g in geom.geoms if isinstance(g, LineString) and not g.is_empty]
    return []


def trim_line_ends(line: LineString, trim_m: float) -> LineString | None:
    """
    Remove distance from both ends of a line.
    """
    if line.length <= 2 * trim_m:
        return None

    start = trim_m
    end = line.length - trim_m

    sampled = [line.interpolate(start)]
    n_steps = max(2, int((end - start) // 10))
    for frac in range(1, n_steps):
        d = start + frac * (end - start) / n_steps
        sampled.append(line.interpolate(d))
    sampled.append(line.interpolate(end))

    trimmed = LineString(sampled)
    if trimmed.length == 0:
        return None
    return trimmed


# ============================================================
# MAIN WORKFLOW
# ============================================================

def main() -> None:
    start_time = time.time()

    print("Reading agricultural fields...")
    if not FIELDS_SHP.exists():
        raise FileNotFoundError(f"Field boundary file not found: {FIELDS_SHP}")
    fields = gpd.read_file(FIELDS_SHP)
    if FIELD_ID_COL not in fields.columns:
        raise ValueError(
            f"Field ID column '{FIELD_ID_COL}' was not found. "
            f"Available columns: {list(fields.columns)}"
        )

    print("Reading OSM roads...")
    if not ROADS_SHP.exists():
        raise FileNotFoundError(f"Road network file not found: {ROADS_SHP}")
    roads = gpd.read_file(ROADS_SHP)
    if ROADS_CLASS_COL not in roads.columns:
        raise ValueError(
            f"Road class column '{ROADS_CLASS_COL}' was not found. "
            f"Available columns: {list(roads.columns)}"
        )

    if KEEP_ROAD_CLASSES is not None:
        roads = roads[roads[ROADS_CLASS_COL].isin(KEEP_ROAD_CLASSES)].copy()

    fields = fields[[FIELD_ID_COL, "geometry"]].copy()
    roads_keep_cols = [ROADS_CLASS_COL, "geometry"]
    if "osm_id" in roads.columns:
        roads_keep_cols.insert(0, "osm_id")
    roads = roads[roads_keep_cols].copy()

    fields = fields[fields.geometry.notnull()].copy()
    roads = roads[roads.geometry.notnull()].copy()

    print("Reprojecting to working CRS...")
    fields = fields.to_crs(WORKING_CRS)
    roads = roads.to_crs(WORKING_CRS)

    if MAX_FIELDS is not None:
        fields = fields.head(MAX_FIELDS).copy()
        print(f"Testing only first {len(fields):,} fields.")
    else:
        print(f"Processing all {len(fields):,} fields.")

    # --------------------------------------------------------
    # 1) PREPARE ROAD PARTS
    # --------------------------------------------------------
    print("Preparing road line parts...")
    t0 = time.time()
    exploded_parts = []

    for _, row in roads.reset_index(drop=False).iterrows():
        road_row_id = row["index"]
        road_class = row[ROADS_CLASS_COL]
        osm_id = row["osm_id"] if "osm_id" in row.index else None

        for part_idx, line in enumerate(iter_lines(row.geometry), start=1):
            exploded_parts.append(
                {
                    "road_row_id": road_row_id,
                    "osm_id": osm_id,
                    "road_class": road_class,
                    "line_part": part_idx,
                    "geometry": line,
                }
            )

    road_parts = gpd.GeoDataFrame(exploded_parts, geometry="geometry", crs=WORKING_CRS)
    print(f"Road parts available: {len(road_parts):,}")
    print(f"Road part preparation time: {(time.time() - t0):.2f} seconds")

    # --------------------------------------------------------
    # 2) FIND ROAD PARTS ADJACENT TO FIELDS
    # --------------------------------------------------------
    print("Finding road parts adjacent to fields...")
    t1 = time.time()

    field_buffers = fields[[FIELD_ID_COL, "geometry"]].copy()
    field_buffers["geometry"] = field_buffers.geometry.buffer(FIELD_TO_ROAD_MAX_DISTANCE_M)

    field_road_pairs = gpd.sjoin(
        road_parts,
        field_buffers,
        how="inner",
        predicate="intersects",
    )

    if field_road_pairs.empty:
        raise ValueError(
            "No road parts intersected the field buffer. "
            "Try increasing FIELD_TO_ROAD_MAX_DISTANCE_M."
        )

    total_pairs = len(field_road_pairs)
    print(f"Field-road adjacency pairs found: {total_pairs:,}")
    print(f"Spatial join time: {(time.time() - t1):.2f} seconds")

    fields_lookup = fields.set_index(FIELD_ID_COL)

    # --------------------------------------------------------
    # 3) FOR EACH FIELD, KEEP ONLY THE BEST FRONTAGE
    # --------------------------------------------------------
    print("Selecting one best frontage per field...")
    t2 = time.time()

    best_frontage_by_field = {}
    frontages_checked = 0
    frontages_valid = 0

    for idx, (_, pair) in enumerate(field_road_pairs.iterrows(), start=1):
        if idx % PROGRESS_EVERY == 0:
            elapsed_loop = time.time() - t2
            print(
                f"Processed {idx:,} / {total_pairs:,} field-road pairs... "
                f"Valid frontages so far: {frontages_valid:,} | "
                f"Elapsed: {elapsed_loop/60:.2f} min"
            )

        field_id = pair[FIELD_ID_COL]
        field_geom = fields_lookup.loc[field_id].geometry
        road_line = pair.geometry

        field_buffer = field_geom.buffer(FIELD_TO_ROAD_MAX_DISTANCE_M)
        frontage_geom = road_line.intersection(field_buffer)

        for frontage_line in iter_lines(frontage_geom):
            frontages_checked += 1

            if frontage_line.length < MIN_FRONTAGE_LENGTH_M:
                continue

            trimmed_frontage = trim_line_ends(frontage_line, FRONTAGE_END_TRIM_M)
            if trimmed_frontage is None or trimmed_frontage.length < MIN_FRONTAGE_LENGTH_M:
                continue

            # Candidate point = center of the frontage
            dist_center = trimmed_frontage.length / 2.0
            road_point = trimmed_frontage.interpolate(dist_center)

            is_curve, curve_deg = is_point_on_strong_curve(
                line=trimmed_frontage,
                distance_along=dist_center,
                half_len=BEARING_SEGMENT_HALF_LENGTH_M,
                max_curve_deg=MAX_LOCAL_CURVE_DEGREES,
            )
            if is_curve:
                continue

            frontages_valid += 1

            candidate = {
                "field_id": field_id,
                "field_geom": field_geom,
                "road_point": road_point,
                "frontage_line": trimmed_frontage,
                "frontage_length_m": trimmed_frontage.length,
                "distance_along_frontage_m": dist_center,
                "curve_deg": curve_deg,
                "osm_id": pair.get("osm_id"),
                "road_row_id": pair["road_row_id"],
                "road_class": pair.get("road_class"),
                "line_part": pair["line_part"],
            }

            # Keep the longest valid frontage for each field
            if field_id not in best_frontage_by_field:
                best_frontage_by_field[field_id] = candidate
            else:
                if candidate["frontage_length_m"] > best_frontage_by_field[field_id]["frontage_length_m"]:
                    best_frontage_by_field[field_id] = candidate

    if not best_frontage_by_field:
        raise ValueError(
            "No valid one-per-field candidate points were created. "
            "Try relaxing FIELD_TO_ROAD_MAX_DISTANCE_M, FRONTAGE_END_TRIM_M, "
            "MIN_FRONTAGE_LENGTH_M, or MAX_LOCAL_CURVE_DEGREES."
        )

    # --------------------------------------------------------
    # 4) BUILD OUTPUT RECORDS
    # --------------------------------------------------------
    print("Building final one-point-per-field records...")
    t3 = time.time()

    records = []
    point_counter = 1

    for field_id, item in best_frontage_by_field.items():
        field_geom = item["field_geom"]
        road_point = item["road_point"]
        frontage_line = item["frontage_line"]
        dist_along = item["distance_along_frontage_m"]

        road_bearing = estimate_local_bearing(
            line=frontage_line,
            distance_along=dist_along,
            half_len=BEARING_SEGMENT_HALF_LENGTH_M,
        )

        field_side = get_side_of_field(road_point, road_bearing, field_geom)
        heading_perp = heading_perpendicular_to_field_side(road_bearing, field_side)

        road_pt, nearest_field_pt = nearest_points(road_point, field_geom)
        heading_center = calculate_bearing(road_pt, nearest_field_pt)
        heading_m20 = normalize_heading(heading_center - HEADING_OFFSET_DEGREES)
        heading_p20 = normalize_heading(heading_center + HEADING_OFFSET_DEGREES)

        field_target_pt = compute_field_target_point(
            road_point=road_point,
            field_geom=field_geom,
            heading_center=heading_center,
            offset_m=FIELD_TARGET_OFFSET_M,
        )

        distance_to_field_edge = road_point.distance(field_geom)

        records.append(
            {
                "point_id": f"MS_PT_{point_counter:07d}",
                "field_id": field_id,
                "selection_mode": "one_per_field",
                "frontage_segment_id": f"BEST_FRONTAGE_{point_counter:07d}",
                "osm_id": item["osm_id"],
                "road_row_id": item["road_row_id"],
                "road_class": item["road_class"],
                "line_part": item["line_part"],
                "frontage_length_m": round(item["frontage_length_m"], 2),
                "points_assigned_to_frontage": 1,
                "distance_along_frontage_m": round(dist_along, 2),
                "distance_to_field_edge_m": round(distance_to_field_edge, 2),
                "road_bearing": round(road_bearing, 2),
                "local_curve_deg": round(item["curve_deg"], 2),
                "field_side": field_side,
                "heading_perpendicular": round(heading_perp, 2),
                "heading_m20": round(heading_m20, 2),
                "heading_0": round(heading_center, 2),
                "heading_p20": round(heading_p20, 2),
                "field_target_x_m": round(field_target_pt.x, 3),
                "field_target_y_m": round(field_target_pt.y, 3),
                "geometry": road_point,
            }
        )
        point_counter += 1

    candidate_gdf = gpd.GeoDataFrame(records, geometry="geometry", crs=WORKING_CRS)
    print(f"Final one-point-per-field candidates created: {len(candidate_gdf):,}")
    print(f"Best-frontage selection time: {(time.time() - t3):.2f} seconds")

    # --------------------------------------------------------
    # 5) CONVERT TO LAT/LON
    # --------------------------------------------------------
    print("Converting candidate points to latitude/longitude...")
    t4 = time.time()

    candidate_wgs84 = candidate_gdf.to_crs(WGS84).copy()
    candidate_wgs84["longitude"] = candidate_wgs84.geometry.x
    candidate_wgs84["latitude"] = candidate_wgs84.geometry.y

    print(f"Coordinate conversion time: {(time.time() - t4):.2f} seconds")

    # --------------------------------------------------------
    # 6) FINAL COLUMNS
    # --------------------------------------------------------
    final_cols = [
        "point_id",
        "field_id",
        "selection_mode",
        "frontage_segment_id",
        "osm_id",
        "road_row_id",
        "road_class",
        "line_part",
        "frontage_length_m",
        "points_assigned_to_frontage",
        "distance_along_frontage_m",
        "distance_to_field_edge_m",
        "road_bearing",
        "local_curve_deg",
        "field_side",
        "heading_perpendicular",
        "heading_m20",
        "heading_0",
        "heading_p20",
        "field_target_x_m",
        "field_target_y_m",
        "latitude",
        "longitude",
        "geometry",
    ]
    candidate_wgs84 = candidate_wgs84[final_cols].copy()

    # --------------------------------------------------------
    # 7) SAVE OUTPUTS
    # --------------------------------------------------------
    output_gpkg = OUTPUT_DIR / "candidate_points_one_per_field.gpkg"
    output_csv = OUTPUT_DIR / "candidate_points_one_per_field.csv"

    print("Saving GeoPackage...")
    t5 = time.time()
    candidate_wgs84.to_file(output_gpkg, layer="candidate_points_one_per_field", driver="GPKG")
    print(f"GeoPackage save time: {(time.time() - t5):.2f} seconds")

    print("Saving CSV...")
    t6 = time.time()
    candidate_wgs84.drop(columns="geometry").to_csv(output_csv, index=False)
    print(f"CSV save time: {(time.time() - t6):.2f} seconds")

    # --------------------------------------------------------
    # 8) SUMMARY
    # --------------------------------------------------------
    total_runtime = time.time() - start_time

    print("\nDone.")
    print(f"GeoPackage saved to: {output_gpkg}")
    print(f"CSV saved to: {output_csv}")

    print("\nSummary:")
    print(f"  - total input fields processed: {len(fields):,}")
    print(f"  - total field-road pairs evaluated: {total_pairs:,}")
    print(f"  - total frontage candidates checked: {frontages_checked:,}")
    print(f"  - valid frontage candidates: {frontages_valid:,}")
    print(f"  - final one-point-per-field outputs: {len(candidate_gdf):,}")
    print(f"  - total runtime: {total_runtime/60:.2f} minutes")

    print("\nKey outputs:")
    print("  - one row = one road point linked to one target field")
    print("  - exactly one point is kept per field")
    print("  - the selected point is the center of the longest valid frontage")
    print("  - frontage edges are trimmed")
    print("  - strong road curves are filtered out")
    print("  - heading_0   = central field-centric heading")
    print("  - heading_m20 = central heading - 20 degrees")
    print("  - heading_p20 = central heading + 20 degrees")
    print("  - geometry remains on the road for Street View requests")


if __name__ == "__main__":
    main()