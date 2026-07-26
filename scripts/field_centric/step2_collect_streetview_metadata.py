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
AGRIVIEW - STEP 2 (STREET VIEW METADATA)
---------------------------------------------------------
This script queries Google Street View metadata for candidate points generated
by the field-centric workflow.

It supports two input modes:
    - "adaptive"
    - "one_per_field"

Set INPUT_MODE below before running.
"""


# ============================================================
# PROJECT PATHS
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent

INPUT_MODE = "one_per_field"  # Options: "adaptive" or "one_per_field"

if INPUT_MODE == "adaptive":
    OUTPUT_DIR = PROJECT_DIR / "outputs" / "field_centric_adaptive"
    INPUT_CSV = OUTPUT_DIR / "candidate_points_adaptive.csv"
elif INPUT_MODE == "one_per_field":
    OUTPUT_DIR = PROJECT_DIR / "outputs" / "field_centric_one_per_field"
    INPUT_CSV = OUTPUT_DIR / "candidate_points_one_per_field.csv"
else:
    raise ValueError("INPUT_MODE must be 'adaptive' or 'one_per_field'.")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# USER SETTINGS
# ============================================================

API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
METADATA_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"

IMAGE_SIZE = "640x640"
SOURCE = "outdoor"
SLEEP_SECONDS = 0.05

# None = process all rows
MAX_ROWS: Optional[int] = None

HEADING_SPECS = [
    ("m20", "heading_m20"),
    ("0", "heading_0"),
    ("p20", "heading_p20"),
]

PROGRESS_EVERY = 20


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


def derive_output_paths(input_csv: Path) -> tuple[Path, Path]:
    stem = input_csv.stem
    full_csv = OUTPUT_DIR / f"{stem}_metadata_full.csv"
    download_csv = OUTPUT_DIR / f"{stem}_metadata_for_download.csv"
    return full_csv, download_csv


def main() -> None:
    start_time = time.time()

    if not API_KEY:
        raise ValueError(
            "Google API key not found. Set GOOGLE_MAPS_API_KEY as an environment variable."
        )

    print(f"Input mode: {INPUT_MODE}")
    print("Reading candidate points CSV...")

    if not INPUT_CSV.exists():
        raise FileNotFoundError(f"Input CSV not found: {INPUT_CSV}")

    df = pd.read_csv(INPUT_CSV)

    required_cols = ["point_id", "field_id", "latitude", "longitude"] + [
        col for _, col in HEADING_SPECS
    ]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in input CSV: {missing}")

    has_field_side = "field_side" in df.columns
    has_frontage_segment_id = "frontage_segment_id" in df.columns
    has_selection_mode = "selection_mode" in df.columns

    if MAX_ROWS is not None:
        df = df.head(MAX_ROWS).copy()
        print(f"Testing first {len(df):,} input rows only.")
    else:
        print(f"Processing all {len(df):,} input rows.")

    total_input_rows = len(df)
    total_expected_requests = total_input_rows * len(HEADING_SPECS)

    records = []
    request_counter = 0

    print("Querying Street View metadata...")
    for i, row in df.iterrows():
        point_id = row["point_id"]
        field_id = row["field_id"]
        field_side = row["field_side"] if has_field_side else None
        frontage_segment_id = row["frontage_segment_id"] if has_frontage_segment_id else None
        selection_mode = row["selection_mode"] if has_selection_mode else INPUT_MODE
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
                    "field_side": field_side,
                    "frontage_segment_id": frontage_segment_id,
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
            print(
                f"Processed {i + 1:,} / {total_input_rows:,} rows | "
                f"Metadata requests: {request_counter:,} / ~{total_expected_requests:,} | "
                f"Elapsed: {elapsed/60:.2f} min"
            )

    metadata_df = pd.DataFrame(records)
    if metadata_df.empty:
        raise ValueError("No metadata records were created.")

    full_csv, download_csv = derive_output_paths(INPUT_CSV)

    metadata_df.to_csv(full_csv, index=False)
    print(f"Full metadata table saved to: {full_csv}")

    ok_df = metadata_df[metadata_df["meta_status"] == "OK"].copy()

    if not ok_df.empty:
        dedupe_cols = ["pano_id", "field_id", "heading_label", "selection_mode"]
        download_df = (
            ok_df.sort_values(["field_id", "point_id", "heading_label", "pano_id"])
            .drop_duplicates(subset=dedupe_cols)
            .copy()
        )
    else:
        download_df = ok_df.copy()

    download_df.to_csv(download_csv, index=False)
    print(f"Download-ready metadata table saved to: {download_csv}")

    elapsed = time.time() - start_time
    print("\nDone.")
    print(f"Metadata rows created: {len(metadata_df):,}")
    print(f"Rows with imagery available: {(metadata_df['meta_status'] == 'OK').sum():,}")
    print(f"Rows kept for download: {len(download_df):,}")
    print(f"Total runtime: {elapsed/60:.2f} minutes")


if __name__ == "__main__":
    main()
