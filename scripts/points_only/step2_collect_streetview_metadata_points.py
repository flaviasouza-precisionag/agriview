# Copyright (c) 2026 Flavia Luize Pereira de Souza.
# All rights reserved. See LICENSE for permitted portfolio-evaluation use.

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests


"""
AGRIVIEW - STEP 2 (POINTS-ONLY METADATA)
----------------------------------------------------
OBJECTIVE
    Query Google Street View metadata for user-provided points prepared in the
    points-only workflow.

MAIN IDEA
    Step 1 in the points-only workflow prepares a standardized candidate-point
    file containing:
        - point_id
        - latitude
        - longitude
        - heading_m20
        - heading_0
        - heading_p20

    This step does NOT download images yet.
    It only asks Google:
        - does imagery exist here?
        - what date is available?
        - what pano_id is returned?
        - what panorama coordinates does Google return?
"""


# ============================================================
# USER SETTINGS
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent.parent
INPUT_CSV = PROJECT_DIR / "outputs" / "points_only" / "candidate_points_points_only.csv"

OUTPUT_DIR = PROJECT_DIR / "outputs" / "points_only"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

METADATA_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"
IMAGE_SIZE = "640x640"
SOURCE = "outdoor"
SLEEP_SECONDS = 0.05

MAX_ROWS: Optional[int] = 300

HEADING_SPECS = [
    ("m20", "heading_m20"),
    ("0", "heading_0"),
    ("p20", "heading_p20"),
]

PROGRESS_EVERY = 20


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def query_metadata(lat: float, lon: float, heading: float) -> dict:
    params = {
        "location": f"{lat},{lon}",
        "heading": heading,
        "size": IMAGE_SIZE,
        "source": SOURCE,
        "key": API_KEY,
    }

    response = requests.get(METADATA_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def parse_metadata_result(result: dict) -> dict:
    location = result.get("location", {}) or {}

    return {
        "meta_status": result.get("status"),
        "meta_date": result.get("date"),
        "pano_id": result.get("pano_id"),
        "returned_latitude": location.get("lat"),
        "returned_longitude": location.get("lng"),
        "copyright": result.get("copyright"),
    }


def build_error_result(error: Exception) -> dict:
    return {
        "meta_status": "ERROR",
        "meta_date": None,
        "pano_id": None,
        "returned_latitude": None,
        "returned_longitude": None,
        "copyright": None,
        "error_message": str(error),
    }


# ============================================================
# MAIN WORKFLOW
# ============================================================

def main() -> None:
    start_time = time.time()

    if not API_KEY:
        raise ValueError("Set GOOGLE_MAPS_API_KEY as an environment variable before running.")

    print("Reading points-only candidate points CSV...")
    df = pd.read_csv(INPUT_CSV)

    required_cols = [
        "point_id",
        "field_id",
        "latitude",
        "longitude",
        "selection_mode",
    ] + [col for _, col in HEADING_SPECS]

    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(
            f"Missing required columns in input CSV: {missing}\n"
            "Make sure INPUT_CSV points to the output of points_only Step 1."
        )

    if MAX_ROWS is not None:
        df = df.head(MAX_ROWS).copy()
        print(f"Testing first {len(df):,} input rows only.")
    else:
        print(f"Processing all {len(df):,} input rows.")

    total_input_rows = len(df)
    total_expected_requests = total_input_rows * len(HEADING_SPECS)

    records = []
    request_counter = 0

    print("Querying Street View metadata for each heading candidate...")
    for i, row in df.iterrows():
        point_id = row["point_id"]
        field_id = row["field_id"]
        selection_mode = row["selection_mode"]
        lat = float(row["latitude"])
        lon = float(row["longitude"])

        for heading_label, heading_col in HEADING_SPECS:
            if pd.isna(row[heading_col]):
                continue

            requested_heading = float(row[heading_col])
            request_counter += 1

            try:
                result = query_metadata(lat, lon, requested_heading)
                parsed = parse_metadata_result(result)
                error_message = None
            except Exception as e:
                parsed = build_error_result(e)
                error_message = str(e)

            records.append(
                {
                    "point_id": point_id,
                    "field_id": field_id,
                    "selection_mode": selection_mode,
                    "field_side": None,
                    "frontage_segment_id": None,
                    "heading_label": heading_label,
                    "heading_column": heading_col,
                    "requested_heading": requested_heading,
                    "latitude": lat,
                    "longitude": lon,
                    **parsed,
                    "error_message": error_message,
                }
            )

            time.sleep(SLEEP_SECONDS)

        if (i + 1) % PROGRESS_EVERY == 0 or (i + 1) == total_input_rows:
            elapsed = time.time() - start_time
            pct_rows = (i + 1) / total_input_rows * 100
            print(
                f"Processed {i + 1:,} / {total_input_rows:,} input rows "
                f"({pct_rows:.1f}%) | "
                f"Metadata requests made: {request_counter:,} / ~{total_expected_requests:,} | "
                f"Elapsed: {elapsed/60:.2f} min"
            )

    metadata_df = pd.DataFrame(records)

    if metadata_df.empty:
        raise ValueError("No metadata records were created.")

    full_csv = OUTPUT_DIR / "candidate_points_points_only_metadata_full.csv"
    download_csv = OUTPUT_DIR / "candidate_points_points_only_metadata_for_download.csv"

    metadata_df.to_csv(full_csv, index=False)
    print(f"Full metadata table saved to: {full_csv}")

    ok_df = metadata_df[metadata_df["meta_status"] == "OK"].copy()

    if not ok_df.empty:
        download_df = (
            ok_df
            .sort_values(["field_id", "point_id", "heading_label", "pano_id"])
            .drop_duplicates(subset=["pano_id", "field_id", "heading_label", "selection_mode"])
            .copy()
        )
    else:
        download_df = ok_df.copy()

    download_df.to_csv(download_csv, index=False)
    print(f"Download-ready metadata table saved to: {download_csv}")

    elapsed = time.time() - start_time

    print("\nDone.")
    print(f"Metadata rows created: {len(metadata_df):,}")
    print(f"Rows with available imagery (status=OK): {(metadata_df['meta_status'] == 'OK').sum():,}")
    print(f"Rows kept for possible download: {len(download_df):,}")
    print(f"Total runtime: {elapsed/60:.2f} minutes")


if __name__ == "__main__":
    main()