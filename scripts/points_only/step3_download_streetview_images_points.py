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
AGRIVIEW - STEP 3 (POINTS-ONLY IMAGE DOWNLOAD)
----------------------------------------------------------
OBJECTIVE
    Download a controlled set of Street View images from the metadata table
    generated in the points-only workflow.

MAIN IDEA
    Step 2 identifies valid panoramas for user-provided points.
    Step 3 takes that metadata table and:
        - filters the rows according to your criteria
        - downloads the images
        - writes a detailed download log

IMPORTANT LIMITATION
    This workflow uses user-provided points and does NOT verify:
        - whether the point is agricultural
        - whether it is near a road
        - whether it is optimally positioned relative to a field
"""


# ============================================================
# USER SETTINGS
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent.parent
INPUT_CSV = PROJECT_DIR / "outputs" / "points_only" / "candidate_points_points_only_metadata_for_download.csv"

OUTPUT_DIR = PROJECT_DIR / "outputs" / "points_only"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

IMAGE_URL = "https://maps.googleapis.com/maps/api/streetview"

# ------------------------------------------------------------
# IMAGE RENDERING SETTINGS
# ------------------------------------------------------------
IMAGE_SIZE = "640x640"
FOV = 75
PITCH = -10
SOURCE = "outdoor"
RETURN_ERROR_CODE = True

# ------------------------------------------------------------
# DOWNLOAD FILTER MODE
# ------------------------------------------------------------
# Options:
#   "any_available" -> download any valid panorama
#   "season_months" -> download only panoramas in selected months
DOWNLOAD_MODE = "season_months"

TARGET_MONTHS = [7, 8, 9]
REQUIRE_EXACT_YEAR_MONTH = True

# ------------------------------------------------------------
# PILOT SIZE
# ------------------------------------------------------------
MAX_ROWS: Optional[int] = 30

# Optional: restrict to only one heading label during pilot
# Examples: None, "0", "m20", "p20"
ONLY_HEADING_LABEL: Optional[str] = None

PROGRESS_EVERY = 10


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def sanitize_filename(value: object) -> str:
    """
    Replace unsafe characters in file name components.
    """
    text = str(value)
    text = re.sub(r"[^\w\-\.]+", "_", text)
    return text.strip("_") or "item"


def parse_metadata_date(date_str: Optional[str]) -> Tuple[Optional[int], Optional[int], str]:
    """
    Parse Street View metadata date into:
        (year, month, granularity)
    """
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
    """
    Decide whether one metadata row should be downloaded.
    """
    if row["meta_status"] != "OK":
        return False

    if ONLY_HEADING_LABEL is not None and str(row.get("heading_label")) != str(ONLY_HEADING_LABEL):
        return False

    year, month, granularity = parse_metadata_date(row.get("meta_date"))

    if DOWNLOAD_MODE == "any_available":
        return True

    if DOWNLOAD_MODE == "season_months":
        if granularity != "year_month":
            return False if REQUIRE_EXACT_YEAR_MONTH else True
        return month in TARGET_MONTHS

    raise ValueError(
        f"Invalid DOWNLOAD_MODE='{DOWNLOAD_MODE}'. Use 'any_available' or 'season_months'."
    )


def build_image_params(row: pd.Series) -> dict:
    """
    Build the Street View image request.
    """
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
    """
    Create the subfolder name based on metadata date.
    """
    if pd.isna(meta_date) or not meta_date:
        return "unknown_date"

    meta_date = str(meta_date)

    if re.fullmatch(r"\d{4}-\d{2}", meta_date):
        return meta_date

    if re.fullmatch(r"\d{4}", meta_date):
        return meta_date

    return "unknown_date"


def build_output_filename(row: pd.Series) -> str:
    """
    Build a readable filename.
    """
    point_id = sanitize_filename(row["point_id"])
    heading_label = sanitize_filename(row.get("heading_label", "h"))
    meta_date = sanitize_filename(row.get("meta_date", "nodate"))
    pano_id = sanitize_filename(row.get("pano_id", "nopano"))

    return f"{point_id}_h{heading_label}_{meta_date}_p{pano_id}.jpg"


# ============================================================
# MAIN WORKFLOW
# ============================================================

def main() -> None:
    start_time = time.time()

    if not API_KEY:
        raise ValueError("Set GOOGLE_MAPS_API_KEY as an environment variable before running.")

    print("Reading metadata-for-download CSV...")
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
        raise ValueError(
            f"Missing required columns in input CSV: {missing}\n"
            "Make sure INPUT_CSV points to the output of points_only Step 2."
        )

    print("Applying date / heading filters...")
    df = df[df.apply(row_matches_download_mode, axis=1)].copy()

    if df.empty:
        raise ValueError(
            "No rows matched the current Step 3 filters.\n"
            "Try changing DOWNLOAD_MODE, TARGET_MONTHS, or ONLY_HEADING_LABEL."
        )

    if MAX_ROWS is not None:
        df = df.head(MAX_ROWS).copy()
        print(f"Downloading first {len(df):,} filtered images only.")
    else:
        print(f"Downloading all {len(df):,} filtered images.")

    total_rows = len(df)

    image_dir = OUTPUT_DIR / "images_candidate_points_points_only"
    image_dir.mkdir(parents=True, exist_ok=True)

    download_log_csv = OUTPUT_DIR / "candidate_points_points_only_download_log.csv"
    download_records = []

    print("Downloading Street View images...")
    for i, (_, row) in enumerate(df.iterrows(), start=1):
        date_folder_name = get_date_folder_name(row["meta_date"])
        date_folder = image_dir / date_folder_name
        date_folder.mkdir(parents=True, exist_ok=True)

        output_name = build_output_filename(row)
        output_path = date_folder / output_name
        params = build_image_params(row)

        http_status_code = None
        response_text = None
        request_url = None

        try:
            response = requests.get(IMAGE_URL, params=params, timeout=60)
            http_status_code = response.status_code
            request_url = response.url

            try:
                response_text = response.text[:500]
            except Exception:
                response_text = None

            response.raise_for_status()

            with open(output_path, "wb") as f:
                f.write(response.content)

            download_status = "OK"
            error_message = None

        except Exception as e:
            download_status = "ERROR"
            error_message = str(e)

        download_records.append(
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
                "date_folder": str(date_folder),
                "download_mode": DOWNLOAD_MODE,
                "pitch_used": PITCH,
                "fov_used": FOV,
                "download_status": download_status,
                "http_status_code": http_status_code,
                "request_url": request_url,
                "response_text": response_text,
                "error_message": error_message,
            }
        )

        if i % PROGRESS_EVERY == 0 or i == total_rows:
            elapsed = time.time() - start_time
            pct = i / total_rows * 100
            print(
                f"Downloaded {i:,} / {total_rows:,} images "
                f"({pct:.1f}%) | "
                f"Elapsed: {elapsed/60:.2f} min"
            )

    log_df = pd.DataFrame(download_records)
    log_df.to_csv(download_log_csv, index=False, escapechar="\\")

    ok_count = (log_df["download_status"] == "OK").sum()
    err_count = (log_df["download_status"] == "ERROR").sum()
    elapsed = time.time() - start_time

    print("\nDone.")
    print(f"Images attempted: {len(log_df):,}")
    print(f"Images downloaded successfully: {ok_count:,}")
    print(f"Image download errors: {err_count:,}")
    print(f"Images folder: {image_dir}")
    print(f"Download log: {download_log_csv}")
    print(f"Total runtime: {elapsed/60:.2f} minutes")


if __name__ == "__main__":
    main()