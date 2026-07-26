# Copyright (c) 2026 Flavia Luize Pereira de Souza.
# All rights reserved. See LICENSE for permitted portfolio-evaluation use.

from __future__ import annotations

import math
import time
from pathlib import Path

import pandas as pd


"""
AGRIVIEW - STEP 1 (POINTS-ONLY INPUT PREPARATION)
-------------------------------------------------------------
OBJECTIVE
    Prepare a clean candidate-point file from user-provided coordinates.

WHEN TO USE THIS WORKFLOW
    Use this mode when the user already has locations and wants to:
        - provide points manually
        - skip field boundaries
        - skip road processing
        - start directly from coordinates

MAIN IDEA
    This script reads a CSV with:
        - point_id
        - latitude
        - longitude
        - optional heading

    It then prepares a standardized output with:
        - heading_0
        - heading_m20
        - heading_p20

WHY THIS EXISTS
    The field-centric workflow is stronger when field polygons and roads exist.

    However, sometimes users only have point locations and still want to test
    Street View retrieval. This script makes that possible.

IMPORTANT LIMITATION
    This workflow does NOT verify:
        - whether the point is agricultural
        - whether the point is near a road
        - whether the point is optimally positioned relative to a field

    It simply prepares user-supplied points for the metadata and download steps.
"""


# ============================================================
# USER SETTINGS
# ============================================================

# Input CSV with user-provided points
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent.parent
INPUT_CSV = PROJECT_DIR / "examples" / "sample_points.csv"

# Output folder
OUTPUT_DIR = PROJECT_DIR / "outputs" / "points_only"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# If heading is missing, use this as the central heading
DEFAULT_HEADING_0 = 0.0

# Heading offsets relative to heading_0
HEADING_OFFSET_DEGREES = 20.0


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def normalize_heading(angle: float) -> float:
    """
    Normalize any heading to the range [0, 360).
    """
    return angle % 360


# ============================================================
# MAIN WORKFLOW
# ============================================================

def main() -> None:
    start_time = time.time()

    print("Reading user-provided points CSV...")
    df = pd.read_csv(INPUT_CSV)

    required_cols = ["point_id", "latitude", "longitude"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(
            f"Missing required columns in input CSV: {missing}\n"
            "The input file must contain at least: point_id, latitude, longitude."
        )

    print("Validating coordinates...")
    if df["latitude"].isna().any() or df["longitude"].isna().any():
        raise ValueError("Latitude and longitude cannot be empty.")

    # Make sure point_id is text
    df["point_id"] = df["point_id"].astype(str)

    # Prepare heading column
    if "heading" not in df.columns:
        df["heading"] = DEFAULT_HEADING_0

    # Fill missing heading with default
    df["heading"] = df["heading"].fillna(DEFAULT_HEADING_0).astype(float)

    print("Building standardized heading columns...")
    df["heading_0"] = df["heading"].apply(normalize_heading)
    df["heading_m20"] = (df["heading_0"] - HEADING_OFFSET_DEGREES).apply(normalize_heading)
    df["heading_p20"] = (df["heading_0"] + HEADING_OFFSET_DEGREES).apply(normalize_heading)

    # Add standard columns for compatibility with downstream steps
    df["selection_mode"] = "points_only"
    df["field_id"] = df["point_id"]   # placeholder for consistency
    df["field_side"] = None
    df["frontage_segment_id"] = None

    # Final output order
    final_cols = [
        "point_id",
        "field_id",
        "selection_mode",
        "field_side",
        "frontage_segment_id",
        "latitude",
        "longitude",
        "heading_m20",
        "heading_0",
        "heading_p20",
    ]
    prepared_df = df[final_cols].copy()

    output_csv = OUTPUT_DIR / "candidate_points_points_only.csv"

    print("Saving prepared points file...")
    prepared_df.to_csv(output_csv, index=False)

    elapsed = time.time() - start_time

    print("\nDone.")
    print(f"Prepared points saved to: {output_csv}")
    print(f"Total points prepared: {len(prepared_df):,}")
    print(f"Total runtime: {elapsed:.2f} seconds")

    print("\nNotes:")
    print("  - heading_0 is the central user-defined or default heading")
    print("  - heading_m20 and heading_p20 are created automatically")
    print("  - this workflow does not validate field or road context")


if __name__ == "__main__":
    main()