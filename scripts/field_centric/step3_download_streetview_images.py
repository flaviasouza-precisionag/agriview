# Copyright (c) 2026 Flavia Luize Pereira de Souza.
# All rights reserved. See LICENSE for permitted portfolio-evaluation use.

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd
import requests


"""
AGRIVIEW - STEP 3 (DOWNLOAD STREET VIEW IMAGES)
---------------------------------------------------------------
Downloads Street View images from metadata tables produced by Step 2.

Set INPUT_MODE below to match the metadata table you want to use:
    - "adaptive"
    - "one_per_field"
"""


# ============================================================
# PROJECT PATHS
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent

INPUT_MODE = "one_per_field"  # Options: "adaptive" or "one_per_field"

if INPUT_MODE == "adaptive":
    OUTPUT_DIR = PROJECT_DIR / "outputs" / "field_centric_adaptive"
    INPUT_CSV = OUTPUT_DIR / "candidate_points_adaptive_metadata_for_download.csv"
elif INPUT_MODE == "one_per_field":
    OUTPUT_DIR = PROJECT_DIR / "outputs" / "field_centric_one_per_field"
    INPUT_CSV = OUTPUT_DIR / "candidate_points_one_per_field_metadata_for_download.csv"
else:
    raise ValueError("INPUT_MODE must be 'adaptive' or 'one_per_field'.")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# USER SETTINGS
# ============================================================

API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
IMAGE_URL = "https://maps.googleapis.com/maps/api/streetview"

IMAGE_SIZE = "640x640"

# Smaller FOV = closer/zoomed-in view.
# Suggested values:
#   75 = wider field context
#   60 = moderately closer
#   35 = stronger zoom
FOV = 60

PITCH = -10
SOURCE = "outdoor"
RETURN_ERROR_CODE = True

DOWNLOAD_MODE = "season_months"  # Options: "any_available" or "season_months"
TARGET_MONTHS = [7, 8, 9]
REQUIRE_EXACT_YEAR_MONTH = True

# None = download all filtered rows
MAX_ROWS: Optional[int] = 30

ONLY_HEADING_LABEL: Optional[str] = None  # Examples: None, "0", "m20", "p20"
PROGRESS_EVERY = 10


def sanitize_filename(value: object) -> str:
    text = str(value)
    text = re.sub(r"[^\w\-\.]+", "_", text)
    return text.strip("_") or "item"


def parse_metadata_date(date_str: Optional[str]) -> Tuple[Optional[int], Optional[int], str]:
    if pd.isna(date_str) or not date_str:
        return None, None, "missing"

    date_str = str(date_str)

    if re.fullmatch(r"\d{4}-\d{2}", date_str):
        year, month = date_str.split("-")
        return int(year), int(month), "year_month"

    if re.fullmatch(r"\d{4}", date_str):
        return int(date_str), None, "year"

    return None, None, "missing"


def row_matches_download_mode(row: pd.Series) -> bool:
    if row["meta_status"] != "OK":
        return False

    if ONLY_HEADING_LABEL is not None and str(row.get("heading_label")) != str(ONLY_HEADING_LABEL):
        return False

    _, month, granularity = parse_metadata_date(row.get("meta_date"))

    if DOWNLOAD_MODE == "any_available":
        return True

    if DOWNLOAD_MODE == "season_months":
        if granularity != "year_month":
            return False if REQUIRE_EXACT_YEAR_MONTH else True
        return month in TARGET_MONTHS

    raise ValueError("Invalid DOWNLOAD_MODE. Use 'any_available' or 'season_months'.")


def build_image_params(row: pd.Series) -> dict:
    params = {
        "size": IMAGE_SIZE,
        "pano": row["pano_id"],
        "heading": float(row["requested_heading"]),
        "fov": FOV,
        "pitch": PITCH,
        "source": SOURCE,
        "key": API_KEY,
    }

    if RETURN_ERROR_CODE:
        params["return_error_code"] = "true"

    return params


def get_date_folder_name(meta_date: object) -> str:
    if pd.isna(meta_date) or not meta_date:
        return "unknown_date"
    return str(meta_date)


def build_output_filename(row: pd.Series) -> str:
    point_id = sanitize_filename(row["point_id"])
    field_id = sanitize_filename(row["field_id"])
    heading_label = sanitize_filename(row.get("heading_label", "h"))
    meta_date = sanitize_filename(row.get("meta_date", "nodate"))
    pano_id = sanitize_filename(row.get("pano_id", "nopano"))
    return f"{point_id}_field_{field_id}_heading_{heading_label}_{meta_date}_pano_{pano_id}.jpg"


def derive_output_paths(input_csv: Path) -> tuple[Path, Path]:
    stem = input_csv.stem
    image_dir = OUTPUT_DIR / f"images_{stem}"
    log_csv = OUTPUT_DIR / f"{stem}_download_log.csv"
    image_dir.mkdir(parents=True, exist_ok=True)
    return image_dir, log_csv


def main() -> None:
    start_time = time.time()

    if not API_KEY:
        raise ValueError(
            "Google API key not found. Set GOOGLE_MAPS_API_KEY as an environment variable."
        )

    print(f"Input mode: {INPUT_MODE}")
    print("Reading metadata-for-download CSV...")

    if not INPUT_CSV.exists():
        raise FileNotFoundError(f"Input CSV not found: {INPUT_CSV}")

    df = pd.read_csv(INPUT_CSV)

    required_cols = [
        "point_id",
        "field_id",
        "heading_label",
        "requested_heading",
        "latitude",
        "longitude",
        "meta_status",
        "meta_date",
        "pano_id",
        "returned_latitude",
        "returned_longitude",
    ]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in input CSV: {missing}")

    print("Applying date and heading filters...")
    df = df[df.apply(row_matches_download_mode, axis=1)].copy()

    if df.empty:
        raise ValueError("No rows matched the current Step 3 filters.")

    if MAX_ROWS is not None:
        df = df.head(MAX_ROWS).copy()
        print(f"Downloading first {len(df):,} filtered images only.")
    else:
        print(f"Downloading all {len(df):,} filtered images.")

    image_dir, download_log_csv = derive_output_paths(INPUT_CSV)
    records = []

    print("Downloading Street View images...")
    for i, (_, row) in enumerate(df.iterrows(), start=1):
        date_folder = image_dir / get_date_folder_name(row["meta_date"])
        date_folder.mkdir(parents=True, exist_ok=True)

        output_name = build_output_filename(row)
        output_path = date_folder / output_name
        params = build_image_params(row)

        try:
            response = requests.get(IMAGE_URL, params=params, timeout=60)
            response.raise_for_status()
            output_path.write_bytes(response.content)
            download_status = "OK"
            error_message = None
            http_status_code = response.status_code
        except Exception as e:
            download_status = "ERROR"
            error_message = str(e)
            http_status_code = None

        records.append(
            {
                "point_id": row["point_id"],
                "field_id": row["field_id"],
                "selection_mode": row.get("selection_mode"),
                "heading_label": row["heading_label"],
                "requested_heading": row["requested_heading"],
                "requested_latitude": row["latitude"],
                "requested_longitude": row["longitude"],
                "returned_latitude": row["returned_latitude"],
                "returned_longitude": row["returned_longitude"],
                "meta_date": row["meta_date"],
                "pano_id": row["pano_id"],
                "image_filename": output_name,
                "image_path": str(output_path),
                "fov_used": FOV,
                "pitch_used": PITCH,
                "download_status": download_status,
                "http_status_code": http_status_code,
                "error_message": error_message,
            }
        )

        if i % PROGRESS_EVERY == 0 or i == len(df):
            elapsed = time.time() - start_time
            print(f"Downloaded {i:,} / {len(df):,} images | Elapsed: {elapsed/60:.2f} min")

    log_df = pd.DataFrame(records)
    log_df.to_csv(download_log_csv, index=False)

    elapsed = time.time() - start_time
    print("\nDone.")
    print(f"Images attempted: {len(log_df):,}")
    print(f"Images downloaded successfully: {(log_df['download_status'] == 'OK').sum():,}")
    print(f"Download log: {download_log_csv}")
    print(f"Total runtime: {elapsed/60:.2f} minutes")


if __name__ == "__main__":
    main()
