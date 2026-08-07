"""Shared helpers for the departure-time OTP prediction pipeline."""

import io
import os
import ssl
import time
import urllib.request
import zipfile

import pandas as pd
import requests


def line_key(name):
    """
    Normalize Regional Rail line names for joining across datasets.

    Collapses variants that source datasets spell/treat differently:
    Media/Elwyn and Media/Wawa (line extension in 2023), and the
    ridership CSV uses "Bala Cynwyd" and "Manyunk/Norristown"(once)
    where GTFS uses "Cynwyd"/"Manayunk/Norristown"(corrected spelling).
    """
    if pd.isna(name):
        return name
    return (
        name.replace("Media/Elwyn", "Media")
        .replace("Media/Wawa", "Media")
        .replace("Bala Cynwyd", "Cynwyd")
        .replace("Manyunk/Norristown", "Manayunk/Norristown")
    )


def remap_to_canonical_stop_id(df, crosswalk, stop_id_col="stop_id", date_col="source_gtfs_date"):
    """
    Remap a release-native stop_id column to its canonical (current-release)
    equivalent, using the cross-release crosswalk built in
    02c_gtfs_stops_crosswalk.ipynb (data/2_stop_id_crosswalk.parquet:
    stop_id, gtfs_date, canonical_stop_id, stop_name_normalized, match_method).

    Rows whose (stop_id, gtfs_date) has no crosswalk entry, or whose
    match_method is "unmatched", are dropped rather than passed through --
    silently keeping a release-native id here is exactly the failure mode
    this crosswalk exists to remove (SEPTA stop_id renumbering across
    historical GTFS releases). Prints how many rows were dropped and why.

    Returns a copy of df with stop_id_col overwritten in place by the
    canonical id (column name unchanged, so callers don't need to adjust
    downstream code beyond this remap step).
    """
    before = len(df)
    # rename the crosswalk's join-key columns to names that can't collide with df's
    # own columns (stop_id_col/date_col are frequently literally "stop_id" and
    # "source_gtfs_date" -- merging two frames that each already have a column
    # named "stop_id" causes pandas to suffix both to stop_id_x/stop_id_y instead
    # of keeping a plain "stop_id", which silently breaks the overwrite below)
    cw = crosswalk[["stop_id", "gtfs_date", "canonical_stop_id", "match_method"]].rename(
        columns={"stop_id": "_cw_stop_id", "gtfs_date": "_cw_gtfs_date"}
    )
    merged = df.merge(
        cw,
        left_on=[stop_id_col, date_col],
        right_on=["_cw_stop_id", "_cw_gtfs_date"],
        how="left",
    )
    no_entry = merged["canonical_stop_id"].isna().sum()
    unmatched = (merged["match_method"] == "unmatched").sum()
    merged = merged[merged["match_method"] != "unmatched"].dropna(subset=["canonical_stop_id"]).copy()
    merged[stop_id_col] = merged["canonical_stop_id"]
    merged = merged.drop(columns=["_cw_stop_id", "_cw_gtfs_date", "canonical_stop_id", "match_method"])
    print(
        f"remap_to_canonical_stop_id: {before:,} rows in, {len(merged):,} out "
        f"({no_entry:,} with no crosswalk entry, {unmatched:,} flagged unmatched)"
    )
    return merged


def fetch_gtfs_releases(cutoff_date="2016-12-16", page_size=100, sleep_sec=2):
    """
    List all SEPTA GTFS release metadata from the GitHub API, newest first,
    back through cutoff_date. Each entry is {"tag": <github tag name>,
    "date": <python date>}.
    """
    all_releases = []
    pg = 1
    done = False
    while not done:
        page = requests.get(
            "https://api.github.com/repos/septadev/GTFS/releases",
            params={"per_page": page_size, "page": pg},
            timeout=10,
        )
        data = page.json()
        if not data:
            break

        for rel in data:
            if rel["published_at"][:10] < cutoff_date:
                # fetching works backwards, so stop once we hit the cutoff
                done = True
                break
            all_releases.append(
                {
                    "tag": rel["tag_name"],
                    "date": pd.to_datetime(rel["published_at"]).date(),
                }
            )
        pg += 1
        time.sleep(sleep_sec)

    return all_releases


def fetch_gtfs_release_zip(tag, timeout=10):
    """
    Download a SEPTA GTFS release and return its `google_rail.zip` as an
    opened ZipFile, or None if the release can't be fetched
    Caller reads whichever files it needs (trips.txt, routes.txt,
    calendar_dates.txt, stop_times.txt, ...) off the returned ZipFile.
    Called by 02_
    """
    url = f"https://github.com/septadev/GTFS/releases/download/{tag}/gtfs_public.zip"
    r = requests.get(url, timeout=timeout)
    if r.status_code != 200:
        return None

    z = zipfile.ZipFile(io.BytesIO(r.content))
    return zipfile.ZipFile(io.BytesIO(z.read("google_rail.zip")))


def fetch_ridership_monthly():
    """
    Fetch SEPTA's public system-wide monthly ridership-by-mode dataset
    (ArcGIS Hub CSV, 2019-present), filtered to Regional Rail.
    Returns columns ref_year/ref_month/ridership.

    Called by 04_ridership.ipynb and 04b_ridership_safe.ipynb.
    """
    system_cert_path = "/etc/ssl/cert.pem"
    ctx = (
        ssl.create_default_context(cafile=system_cert_path)
        if os.path.exists(system_cert_path)
        else ssl.create_default_context()
    )
    url = "https://hub.arcgis.com/api/v3/datasets/6a90b08258ba42359bffaded8d6494d4_0/downloads/data?format=csv&spatialRefId=4326&where=1%3D1"
    with urllib.request.urlopen(url, context=ctx, timeout=30) as resp:
        rr_raw = pd.read_csv(resp)

    rr_monthly = rr_raw[rr_raw["Mode"] == "Regional Rail"][
        ["Calendar_Year", "Calendar_Month", "Average_Daily_Ridership"]
    ].copy()
    rr_monthly = rr_monthly.rename(
        columns={
            "Calendar_Year": "ref_year",
            "Calendar_Month": "ref_month",
            "Average_Daily_Ridership": "ridership",
        }
    )
    return rr_monthly.sort_values(["ref_year", "ref_month"]).reset_index(
        drop=True
    )


WEATHER_COVERAGE_CUTOFF = "2025-08-26"


def load_model_split(basepath):
    """
    Load 7_df_departure_ready.parquet, build is_otp, and split into
    train (year <= 2024) / test (year == 2025, restricted to
    WEATHER_COVERAGE_CUTOFF -- final_weather_ks.parquet's coverage ends
    there, in late Aug 2025).
    Used in 09_prediction.ipynb and subsequently
    """
    df = pd.read_parquet(f"{basepath}/7_df_departure_ready.parquet")
    df["is_otp"] = (df["lateness"] < 6).astype(int)
    df["is_inbound"] = df["is_inbound"].astype(bool)
    # is_first/last_run_of_day carry NAs (unresolved schedule, ~0.1% of rows)
    # fillna(False) first so astype(bool) doesn't raise on ambiguous case
    df["is_first_run_of_day"] = (
        df["is_first_run_of_day"].fillna(False).astype(bool)
    )
    df["is_last_run_of_day"] = (
        df["is_last_run_of_day"].fillna(False).astype(bool)
    )

    train_df = df[df["year"] <= 2024].copy()
    test_full = df[df["year"] == 2025].copy()
    test_df = test_full[
        test_full["service_date"] <= WEATHER_COVERAGE_CUTOFF
    ].copy()
    return train_df, test_df


# Validated features for modeling
GBM_FEATURES = [
    "line",
    "is_inbound",
    "sched_origin_sec",
    "sched_duration_sec",
    "n_scheduled_stops_pctile_route",
    "sched_headway_prior_min",
    "sched_headway_next_min",
    "sched_turnaround_gap_min",
    "is_first_run_of_day",
    "is_last_run_of_day",
    "day_of_week",
    "is_holiday",
    "is_day_before_holiday",
    "is_day_after_holiday",
    "am_peak_overlap_bin",
    "midday_overlap_bin",
    "pm_peak_x_sports",
    "wx_primary_asof_collapsed",
    "temp_c_asof",
    "temp_c_3h",
    "temp_c_6h",
    "temp_c_24h",
    "dewpt_c_asof",
    "dewpt_c_24h",
    "wind_speed_ms_asof",
    "wind_speed_ms_3h",
    "wind_speed_ms_6h",
    "wind_speed_ms_24h",
    "gust_ms_asof",
    "gust_ms_3h",
    "gust_ms_6h",
    "gust_ms_24h",
    "precip_mm_asof",
    "precip_mm_3h",
    "precip_mm_6h",
    "precip_mm_24h",
    "vis_m_asof",
    "vis_m_3h",
    "vis_m_6h",
    "vis_m_24h",
    "ceiling_m_asof",
    "ceiling_m_6h",
    "ceiling_m_24h",
    "ice_accretion_cm_asof",
    "ice_accretion_cm_24h",
    "slp_hpa_asof",
    "pressure_3hr_change_hpa_asof",
    "any_precip_6h",
    "any_precip_24h",
    "any_gust_6h",
    "any_gust_24h",
    "any_ice_6h",
    "any_ice_24h",
    "lag_line_lateness_min",
    "lag_line_lateness_std",
    "lag_line_lateness_mean_run",
    "lag_network_lateness_30min_mean",
    "lag_network_lateness_2hr_mean",
    "lag_network_lateness_6hr_mean",
    "lag_line_lateness_1d_mean",
    "lag_line_lateness_7d_mean",
    "lag_line_lateness_30d_mean",
    "lag_train_lateness_1run_mean",
    "lag_train_lateness_3run_mean",
    "lag_train_lateness_5run_mean",
    "lag_train_lateness_10run_mean",
    "avg_daily_boards_monthly",
    "service_additions",
    "service_removals",
    "any_service_exception",
    "stops_from_cc",
]

GBM_CAT_FEATURES = [
    "line",
    "wx_primary_asof_collapsed",
    "day_of_week",
    "am_peak_overlap_bin",
    "midday_overlap_bin",
    "pm_peak_x_sports",
]


def prep_gbm_matrices(
    train_df, test_df, features=GBM_FEATURES, cat_features=GBM_CAT_FEATURES
):
    """
    Build train/test feature matrices for a tree model
    """
    X_train = train_df[features].copy()
    X_test = test_df[features].copy()
    for c in cat_features:
        X_train[c] = X_train[c].astype("category")
        X_test[c] = pd.Categorical(
            X_test[c], categories=X_train[c].cat.categories
        )
    # LightGBM rejects object-dtype columns.
    # Coerce any remaining non-categorical object column to float
    # (True/False -> 1.0/0.0, None -> NaN) so every GBM accepts same matrix.
    for c in features:
        if c not in cat_features and X_train[c].dtype == object:
            X_train[c] = X_train[c].astype(float)
            X_test[c] = X_test[c].astype(float)
    return X_train, X_test
