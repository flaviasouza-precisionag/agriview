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
AGRIVIEW - STEP 1 (FIELD-CENTRIC, ADAPTIVE SAMPLING)
---------------------------------------------------------------------------------
OBJECTIVE
    Create Google Street View candidate points using a FIELD-CENTRIC strategy,
    with:
        1) controlled number of points per frontage
        2) better trimming of frontage ends
        3) improved removal of small leftover frontage segments
        4) removal of points located on strong road curves

GENERAL IDEA
    Earlier versions generated too many candidate points because they sampled
    at a fixed spacing along long frontages.

    V4 improved this by:
        - adapting the number of points to frontage length
        - placing points by fractions of the valid frontage line
        - trimming frontage ends more aggressively
        - filtering very short leftover frontage pieces

    However, after visual inspection of the resulting points and downloaded
    images, you observed that:
        - most points were good
        - but a few points still landed on visibly curved road sections

    Therefore, V5 keeps all the good logic from V4 and adds one more filter:

        - strong-curve filtering

WHAT THE NEW CURVE FILTER DOES
    For each candidate point, the code compares:
        - road direction just BEFORE the point
        - road direction just AFTER the point

    If the change in direction is too large, the point is considered to lie
    on a strong curve and is discarded.

WHY THIS IS USEFUL
    Points on strong curves often produce:
        - images dominated by road shape
        - angled / awkward viewpoints
        - less stable views of the field

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
OUTPUT_DIR = PROJECT_DIR / "outputs" / "field_centric_adaptive"
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
OUTPUT_DIR = PROJECT_DIR / "outputs" / "field_centric_adaptive"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# ANALYSIS SETTINGS
# ============================================================

# Keep all road classes for now.
# We can filter later if needed.
KEEP_ROAD_CLASSES = None

# CRS used for geometry calculations in meters
WORKING_CRS = "EPSG:5070"

# CRS used for final latitude/longitude export
WGS84 = "EPSG:4326"

# ------------------------------------------------------------
# FIELD-ROAD ADJACENCY DISTANCE
# ------------------------------------------------------------
# This value defines which road parts are considered close enough
# to a field to serve as candidate frontage.
#
# We KEEP 30 m here because:
#   - some roads at ~25 m from the field still looked valid
#   - lowering this to 20 m may remove useful roads
FIELD_TO_ROAD_MAX_DISTANCE_M = 30

# ------------------------------------------------------------
# MINIMUM FRONTAGE LENGTH
# ------------------------------------------------------------
# After clipping the road by the field buffer and trimming both ends,
# we only keep the frontage if the remaining line is still long enough.
#
# Why keep 35 m?
# Because very small leftover frontage pieces often correspond to
# unstable corner/cut fragments that do not produce good viewpoints.
MIN_FRONTAGE_LENGTH_M = 35

# ------------------------------------------------------------
# FRONTAGE END TRIM
# ------------------------------------------------------------
# This removes distance from BOTH ends of the frontage line.
#
# Why keep 30 m?
# Because you observed that smaller trimming still allowed too many points:
#   - in corners
#   - near curves
#   - in semi-blind edge positions
#
# 30 m cuts more from the frontage ends and keeps the central, more stable part.
FRONTAGE_END_TRIM_M = 30

# ------------------------------------------------------------
# LOCAL ROAD BEARING WINDOW
# ------------------------------------------------------------
# Half-length used to estimate road direction around a point.
# The code looks slightly before and after the point to estimate
# local road bearing.
BEARING_SEGMENT_HALF_LENGTH_M = 10

# ------------------------------------------------------------
# INTERNAL FIELD TARGET OFFSET
# ------------------------------------------------------------
# Used only as a geometric reference inside the field.
# It does NOT move the Street View request point into the field.
FIELD_TARGET_OFFSET_M = 30

# ------------------------------------------------------------
# HEADING OFFSETS
# ------------------------------------------------------------
# Central heading is the field-facing heading.
# We also keep +/- 20° offsets for alternative views.
HEADING_OFFSET_DEGREES = 20

# ------------------------------------------------------------
# STRONG CURVE FILTER
# ------------------------------------------------------------
# NEW IN V5
#
# We only want to discard points located on more clearly curved road segments,
# not small local irregularities.
#
# This value means:
#   - compare the road direction just before the point and just after the point
#   - if the angular change is greater than 30°, discard the point
#
# Why 30°?
# Because it targets stronger curves while keeping small bends.
MAX_LOCAL_CURVE_DEGREES = 30

# Optional limit for quick testing
# Use None to process all fields
MAX_FIELDS = None

# Print progress every N field-road pairs
PROGRESS_EVERY = 5000

# Maximum number of points allowed on a frontage
MAX_POINTS_PER_FRONTAGE = 8


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def normalize_heading(angle: float) -> float:
    """
    Normalize any angle to the range [0, 360).

    Examples:
        -10  -> 350
        370  -> 10
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

    Why this exists
    ---------------
    Headings wrap around at 360 degrees.
    For example:
        350° and 10° are only 20° apart, not 340° apart.
    """
    diff = abs(a1 - a2) % 360
    return min(diff, 360 - diff)


def estimate_local_bearing(line: LineString, distance_along: float, half_len: float) -> float:
    """
    Estimate local road direction near a point.

    Why local bearing?
    Because roads may curve. We do not want the direction of the whole road,
    only the direction near that candidate point.
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
    Check whether a point lies on a strong curve.

    How it works
    ------------
    We compare:
        - the road direction just BEFORE the point
        - the road direction just AFTER the point

    If the angular change is greater than the allowed threshold,
    the point is considered to lie on a strong curve.

    Returns
    -------
    (is_strong_curve, curve_deg)
        is_strong_curve : bool
        curve_deg       : float
    """
    if line.length == 0:
        return False, 0.0

    # Distances used to measure local direction before and after the point
    d0 = max(0.0, distance_along - half_len)
    d1 = distance_along
    d2 = min(line.length, distance_along + half_len)

    # If the point is too close to start or end, use a fallback
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

    Logic:
        1. Build a local road direction vector
        2. Build a vector from road point toward the field
        3. Use the sign of the cross product

    Result:
        - positive cross product -> left
        - negative cross product -> right
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
    Create a perpendicular heading pointing toward the side containing the field.

    This heading is not the main one we download, but we keep it because it is
    useful as a diagnostic field-side heading.
    """
    if field_side == "left":
        return normalize_heading(road_bearing - 90)
    return normalize_heading(road_bearing + 90)


def move_point_along_heading(point: Point, heading_deg: float, distance_m: float) -> Point:
    """
    Move a point by a given distance in the direction of a compass heading.

    Used here only to create an internal reference point inside the field.
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

    IMPORTANT
    This point is NOT used as the Google request location.
    It is only stored as a geometric reference.

    Fallback logic:
        - try offset point along heading
        - if not inside field, try midpoint between nearest edge point and centroid
        - if still not good, use centroid
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

    Why do this?
    Because frontage ends often correspond to:
        - corners
        - curves
        - blind edge positions
        - unstable field-road relationships

    If the line is too short after trimming, return None.
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


def choose_n_points_by_frontage_length(frontage_length_m: float) -> int:
    """
    Decide how many candidate points to place on a frontage.

    Adaptive strategy:
        - up to 100 m     -> 1 point
        - 100 to 250 m    -> 2 points
        - 250 to 450 m    -> 3 points
        - 450 to 700 m    -> 4 points
        - 700 to 1000 m   -> 5 points
        - 1000 to 1500 m  -> 6 points
        - 1500 to 2000 m  -> 7 points
        - above 2000 m    -> 8 points max
    """
    if frontage_length_m <= 100:
        return 1
    if frontage_length_m <= 250:
        return 2
    if frontage_length_m <= 450:
        return 3
    if frontage_length_m <= 700:
        return 4
    if frontage_length_m <= 1000:
        return 5
    if frontage_length_m <= 1500:
        return 6
    if frontage_length_m <= 2000:
        return 7
    return MAX_POINTS_PER_FRONTAGE


def get_fraction_positions(n_points: int) -> List[float]:
    """
    Return relative positions along the valid frontage line.

    The idea is:
        - avoid the very ends
        - spread points proportionally
        - include the center when appropriate

    Examples:
        1 point -> 50%
        3 points -> 25%, 50%, 75%
        5 points -> 15%, 35%, 50%, 65%, 85%
    """
    fraction_lookup = {
        1: [0.50],
        2: [0.33, 0.67],
        3: [0.25, 0.50, 0.75],
        4: [0.20, 0.40, 0.60, 0.80],
        5: [0.15, 0.35, 0.50, 0.65, 0.85],
        6: [0.10, 0.25, 0.40, 0.60, 0.75, 0.90],
        7: [0.08, 0.22, 0.36, 0.50, 0.64, 0.78, 0.92],
        8: [0.06, 0.18, 0.30, 0.42, 0.58, 0.70, 0.82, 0.94],
    }
    if n_points not in fraction_lookup:
        raise ValueError(f"Unsupported n_points={n_points}. Expected 1 to 8.")
    return fraction_lookup[n_points]


def generate_representative_points_along_line(line: LineString) -> List[Tuple[float, Point]]:
    """
    Generate a controlled number of representative points along the line.

    Instead of fixed spacing, this function:
        1. decides how many points the frontage deserves
        2. places them by fractional positions

    Returns:
        list of tuples = (distance_along_line_m, point_geom)
    """
    if line.length == 0:
        return []

    n_points = choose_n_points_by_frontage_length(line.length)
    fractions = get_fraction_positions(n_points)

    output: List[Tuple[float, Point]] = []
    for frac in fractions:
        distance_along = line.length * frac
        output.append((distance_along, line.interpolate(distance_along)))

    return output


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

    # Keep only needed columns
    fields = fields[[FIELD_ID_COL, "geometry"]].copy()
    roads_keep_cols = [ROADS_CLASS_COL, "geometry"]
    if "osm_id" in roads.columns:
        roads_keep_cols.insert(0, "osm_id")
    roads = roads[roads_keep_cols].copy()

    # Remove null geometry rows
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
    # 1) EXPLODE ROAD GEOMETRIES INTO SIMPLE LINE PARTS
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

    # Lookup to quickly retrieve field geometry by Field_ID
    fields_lookup = fields.set_index(FIELD_ID_COL)

    records = []
    point_counter = 1
    frontage_counter = 1
    valid_frontage_count = 0
    fields_with_points = set()
    points_removed_by_curve = 0

    # --------------------------------------------------------
    # 3) BUILD VALID FRONTAGES AND GENERATE POINTS
    # --------------------------------------------------------
    print("Creating field-centric frontage segments and candidate points...")
    t2 = time.time()

    for idx, (_, pair) in enumerate(field_road_pairs.iterrows(), start=1):
        if idx % PROGRESS_EVERY == 0:
            elapsed_loop = time.time() - t2
            print(
                f"Processed {idx:,} / {total_pairs:,} field-road pairs... "
                f"Current candidate points: {len(records):,} | "
                f"Removed by curve filter: {points_removed_by_curve:,} | "
                f"Elapsed in frontage loop: {elapsed_loop/60:.2f} min"
            )

        field_id = pair[FIELD_ID_COL]
        field_geom = fields_lookup.loc[field_id].geometry
        road_line = pair.geometry

        # Clip road line by field proximity buffer
        field_buffer = field_geom.buffer(FIELD_TO_ROAD_MAX_DISTANCE_M)
        frontage_geom = road_line.intersection(field_buffer)

        for frontage_line in iter_lines(frontage_geom):
            # Skip very short raw frontage
            if frontage_line.length < MIN_FRONTAGE_LENGTH_M:
                continue

            # Remove unstable ends
            trimmed_frontage = trim_line_ends(frontage_line, FRONTAGE_END_TRIM_M)

            # Skip if too short after trimming
            if trimmed_frontage is None or trimmed_frontage.length < MIN_FRONTAGE_LENGTH_M:
                continue

            valid_frontage_count += 1
            frontage_segment_id = f"FRONTAGE_{frontage_counter:07d}"
            frontage_counter += 1

            # Controlled number of representative points
            pts = generate_representative_points_along_line(trimmed_frontage)

            for dist_along_frontage, road_point in pts:
                # ----------------------------------------------------
                # NEW IN V5: REMOVE POINTS ON STRONG CURVES
                # ----------------------------------------------------
                is_curve, curve_deg = is_point_on_strong_curve(
                    line=trimmed_frontage,
                    distance_along=dist_along_frontage,
                    half_len=BEARING_SEGMENT_HALF_LENGTH_M,
                    max_curve_deg=MAX_LOCAL_CURVE_DEGREES,
                )

                if is_curve:
                    points_removed_by_curve += 1
                    continue

                road_bearing = estimate_local_bearing(
                    line=trimmed_frontage,
                    distance_along=dist_along_frontage,
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
                n_points_frontage = choose_n_points_by_frontage_length(trimmed_frontage.length)

                records.append(
                    {
                        "point_id": f"MS_PT_{point_counter:07d}",
                        "field_id": field_id,
                        "frontage_segment_id": frontage_segment_id,
                        "osm_id": pair.get("osm_id"),
                        "road_row_id": pair["road_row_id"],
                        "road_class": pair.get("road_class"),
                        "line_part": pair["line_part"],
                        "frontage_length_m": round(trimmed_frontage.length, 2),
                        "points_assigned_to_frontage": n_points_frontage,
                        "distance_along_frontage_m": round(dist_along_frontage, 2),
                        "distance_to_field_edge_m": round(distance_to_field_edge, 2),
                        "road_bearing": round(road_bearing, 2),
                        "local_curve_deg": round(curve_deg, 2),
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
                fields_with_points.add(field_id)

    if not records:
        raise ValueError(
            "No field-centric candidate points were created. "
            "Try relaxing FIELD_TO_ROAD_MAX_DISTANCE_M, FRONTAGE_END_TRIM_M, "
            "MIN_FRONTAGE_LENGTH_M, or MAX_LOCAL_CURVE_DEGREES."
        )

    frontage_loop_time = time.time() - t2

    candidate_gdf = gpd.GeoDataFrame(records, geometry="geometry", crs=WORKING_CRS)
    print(f"Candidate field-centric points created: {len(candidate_gdf):,}")
    print(f"Points removed by curve filter: {points_removed_by_curve:,}")
    print(f"Frontage generation time: {frontage_loop_time/60:.2f} minutes")

    # --------------------------------------------------------
    # 4) CONVERT TO LAT/LON
    # --------------------------------------------------------
    print("Converting candidate points to latitude/longitude...")
    t3 = time.time()

    candidate_wgs84 = candidate_gdf.to_crs(WGS84).copy()
    candidate_wgs84["longitude"] = candidate_wgs84.geometry.x
    candidate_wgs84["latitude"] = candidate_wgs84.geometry.y

    print(f"Coordinate conversion time: {(time.time() - t3):.2f} seconds")

    # --------------------------------------------------------
    # 5) FINAL COLUMNS
    # --------------------------------------------------------
    final_cols = [
        "point_id",
        "field_id",
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
    # 6) SAVE OUTPUTS
    # --------------------------------------------------------
    output_gpkg = OUTPUT_DIR / "candidate_points_adaptive.gpkg"
    output_csv = OUTPUT_DIR / "candidate_points_adaptive.csv"

    print("Saving GeoPackage...")
    t4 = time.time()
    candidate_wgs84.to_file(output_gpkg, layer="candidate_points_v5", driver="GPKG")
    print(f"GeoPackage save time: {(time.time() - t4):.2f} seconds")

    print("Saving CSV...")
    t5 = time.time()
    candidate_wgs84.drop(columns="geometry").to_csv(output_csv, index=False)
    print(f"CSV save time: {(time.time() - t5):.2f} seconds")

    # --------------------------------------------------------
    # 7) SUMMARY
    # --------------------------------------------------------
    total_runtime = time.time() - start_time
    unique_fields_with_points = len(fields_with_points)
    avg_points_per_frontage = len(candidate_gdf) / valid_frontage_count if valid_frontage_count > 0 else 0
    avg_points_per_field = len(candidate_gdf) / unique_fields_with_points if unique_fields_with_points > 0 else 0

    print("\nDone.")
    print(f"GeoPackage saved to: {output_gpkg}")
    print(f"CSV saved to: {output_csv}")

    print("\nSummary:")
    print(f"  - total input fields processed: {len(fields):,}")
    print(f"  - total field-road pairs evaluated: {total_pairs:,}")
    print(f"  - valid frontage segments kept: {valid_frontage_count:,}")
    print(f"  - unique fields with at least one point: {unique_fields_with_points:,}")
    print(f"  - total candidate points kept: {len(candidate_gdf):,}")
    print(f"  - points removed by strong-curve filter: {points_removed_by_curve:,}")
    print(f"  - average points per frontage: {avg_points_per_frontage:.2f}")
    print(f"  - average points per field with points: {avg_points_per_field:.2f}")
    print(f"  - total runtime: {total_runtime/60:.2f} minutes")

    print("\nKey outputs:")
    print("  - one row = one road point linked to one target field")
    print("  - number of points per frontage is adaptive, not fixed by spacing")
    print("  - points are distributed by fractions of the valid frontage line")
    print("  - frontage ends are trimmed aggressively")
    print("  - short leftover frontage segments are filtered out")
    print("  - strong road curves are filtered out in V5")
    print("  - heading_0   = central field-centric heading")
    print("  - heading_m20 = central heading - 20 degrees")
    print("  - heading_p20 = central heading + 20 degrees")
    print("  - geometry remains on the road for Street View requests")


if __name__ == "__main__":
    main()