# 1: GTFS Merge
#
# This script loads the cleaned OTP data, fetches historical GTFS
# train-to-line linkages from the SEPTA GitHub API, and merges them.
#
# Output is saved as df_gtfs_linked.parquet for use in future scripts.
#
# To re-fetch from GitHub API (already done once to grab through April 2026),
# uncomment the API fetch section and re-save.
#
# CONFIG: set BASEPATH to your local data directory before running
# -----------------------------------------------------------------------

BASEPATH = "data"  # local path to data directory (update as needed)

# -----------------------------------------------------------------------

import requests, zipfile, io
import pandas as pd
from datasets import load_dataset
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import time # for rate limiting


# -----------------------------------------------------------------------
# Load Cleaned OTP Data
# -----------------------------------------------------------------------

# load cleaned OTP dataset saved in 0_otp_load_clean
df = pd.read_parquet(f"{BASEPATH}/df_clean.parquet")
print(f"Loaded {len(df):,} rows")


# -----------------------------------------------------------------------
# GTFS: Single Snapshot (for reference only - skip run)
# -----------------------------------------------------------------------
# The cells below show an initial attempt using just the most recent GTFS snapshot.
# Skip to 'GTFS: Historical API Fetch' for the full historical approach.
# Kept for reference to show why the single snapshot wasn't sufficient.

# Load GTFS
# pull current SEPTA GTFS
r = requests.get("https://github.com/septadev/GTFS/releases/latest/download/gtfs_public.zip")
# unzip
z = zipfile.ZipFile(io.BytesIO(r.content))
# check contents
# print(z.namelist())
# output: ['google_bus.zip', 'google_rail.zip']

# unzip again
rail_zip = zipfile.ZipFile(io.BytesIO(z.read("google_rail.zip")))
# bus_zip = zipfile.ZipFile(io.BytesIO(z.read("google_bus.zip"))) # for bus data

# check contents
# print(rail_zip.namelist())
# output:
# ['stop_times.txt', 'shapes.txt', 'calendar_dates.txt',
# 'route_stops.txt', 'agency.txt', 'routes.txt', 'trips.txt',
# 'stops.txt', 'directions.txt', 'calendar.txt', 'feed_info.txt']

# Load just the trip + route files
trips = pd.read_csv(rail_zip.open("trips.txt"))
# one row per trip i.e. individual train run on specific day
# trips contains:
# -- route_id (short line code)
# -- trip_id (unique for row)
# -- block_id (train no.)
# -- trip_short_name (route_id + block_id, e.g. 'LAN9000')

routes = pd.read_csv(rail_zip.open("routes.txt"))
# one row per train line
# routes contains:
# -- route_id (short line code e.g. TRE)
# -- route_long_name (full line name e.g. Trenton Line)
# -- +metadata

# Build a trip_id -> train line name lookup, requiring info from both dfs
route_lookup = trips.merge(routes[["route_id", "route_long_name"]], on = "route_id")

route_lookup["block_id"] = route_lookup["block_id"].astype(str)

# Merge line names to df
df = (df.merge(
    route_lookup[["block_id", "route_long_name"]].drop_duplicates(),
    left_on = "train_number",
    right_on = "block_id",
    how = "left"
    ).drop(columns = "block_id")
    .rename(columns = {"route_long_name": "line"}))

# Check match rate
n_matched = df["line"].notna().sum()
n_total = len(df)
print(f"Matched: {n_matched:,} / {n_total:,} ({n_matched / n_total * 100:.1f}%)")

# Check for issue of coverage in earlier years
# Thinking that lines/numbering is likely to have adjusted over time
print(df[df["line"].isna()].groupby(df["year"]).size())

# How much coverage do we get across all trains
print("Unmatched train numbers:",
      len(set(df[df["line"].isna()]["train_number"].unique()) - set(route_lookup["block_id"].unique())),
      "/ Total train numbers:", df["train_number"].nunique())

# Not good ^^

# plot unmatched vs. matched over time
summary = pd.DataFrame({"Matched": (df[df["line"].notna()]
                        .groupby(df["year"])["train_number"]
                        .nunique()),

                        "Unmatched": (df[df["line"].isna()]
                        .groupby(df["year"])["train_number"]
                        .nunique())})

fig, ax = plt.subplots()
summary.plot(kind = "bar", stacked = True, ax = ax,
             color = ["seagreen", "firebrick"], edgecolor = "white")
ax.set_title("Matched vs. unmatched train numbers by year (GTFS)")
ax.set_xlabel("")
ax.set_ylabel("Unique train numbers")
ax.tick_params(axis = "x", rotation = 30)
ax.legend(loc = "upper right")
plt.tight_layout()
plt.savefig(f"{BASEPATH}/gtfs_single_snapshot_coverage.png", dpi = 150)
plt.show()

# Unmatched train numbers are high across all years - April 2026 GTFS is missing
# a lot of train information.
# Successful matches climb in the more recent years but recent GTFS merge alone
# is not sufficient for analyzing at all comprehensively at the line level.
# Per OpenDataPhilly, Septa publishes releases to Github. Can see 36 pages of
# releases going back to 2015. Will pull these directly using Github API.


# -----------------------------------------------------------------------
# GTFS: Historical API Fetch
# -----------------------------------------------------------------------
# Can skip most of this code since we have GTFS linkages saved to Drive.
# Load from Drive by running the cell in 'Load GTFS from Drive' below instead.
#
# If linkages are out of date, check the latest release tag on the
# SEPTA GTFS GitHub (https://github.com/septadev/GTFS/releases) and add it to
# the "Add New Releases" section below before loading.

# helper function
# extracts a train number -> line name lookup table from a single GTFS release
def get_train_line_linkage(tag, date):
    url = f"https://github.com/septadev/GTFS/releases/download/{tag}/gtfs_public.zip"
    r = requests.get(url, timeout = 10)
    if r.status_code != 200:
        return None
    # GTFS zip has two inner zips - google_rail and google_bus
    z = zipfile.ZipFile(io.BytesIO(r.content))
    rail_zip = zipfile.ZipFile(io.BytesIO(z.read("google_rail.zip")))

    # one row per train run w/ block_id (train no.) and route
    trips = pd.read_csv(rail_zip.open("trips.txt"),
                        usecols = ["route_id", "block_id"])
    # one row per line w/ full line name
    routes = pd.read_csv(rail_zip.open("routes.txt"),
                         usecols = ["route_id", "route_long_name"])
    routes = routes[routes["route_type"] == 2] # commuter rail only

    # merge to get train id + full name (block_id, route_long_name) and drop dups
    linkage = (trips.merge(routes, on = "route_id")[
        ["block_id", "route_long_name"]]
               .drop_duplicates())
    # preserve date. this way we can attempt to match multi-line trains
    # to the correct line on a given date:
    linkage["gtfs_date"] = date
    return linkage


# Load GTFS from Drive
# Run this and skip to "Merge Train to Line by Date" if up to date
gtfs = pd.read_parquet(f"{BASEPATH}/gtfs_linkages_since_2017_clean.parquet")

# If NOT up to date, refer to recent release's tag name
# directly and add in here by running this section.
# Then skip to "Merge Train to Line by Date"
new_tags = [
    #"v202605240" # add new tags here as they're released
]

if new_tags:
    new_linkages = []
    for tag in new_tags:
        linkage = get_train_line_linkage(tag, tag[1:9])  # extract date from tag name
        if linkage is not None:
            new_linkages.append(linkage)
        time.sleep(0.5)

    new_gtfs = (pd.concat(new_linkages, ignore_index = True)
                  .drop_duplicates()
                  .astype({"block_id": str})
                  .rename(columns = {"block_id": "train_number",
                                     "route_long_name": "line"}))
    new_gtfs["gtfs_date"] = pd.to_datetime(new_gtfs["gtfs_date"])

    # combine with old version, drop dups, re-save
    gtfs = pd.concat([gtfs, new_gtfs], ignore_index = True).drop_duplicates()
    gtfs.to_parquet(f"{BASEPATH}/gtfs_linkages_since_2017_clean.parquet")


# Original Github Fetch (skip if linkages already saved)
# -----------------------------------------------------------------------
#GITHUB API - pulling releases from 2017 onward
# NOTE: first GTFS release in 2017 is Jan 21 -- we pull from last 2016 release
# to capture early Jan 2017 train assignments
#all_releases = []
#pg = 1
#done = False
#while not done:
#    page = requests.get(f"https://api.github.com/repos/septadev/GTFS/releases?per_page=100&page={pg}") # pull
#    data = page.json()
#    if not data: # nothing came back
#        break
#
#    for rel in data:
#        # not int. in pre-2017, but first release in 2017 is Jan 21
#        # filter from last release in 2016 to capture Jan 2017 train assignments
#        if rel["published_at"][:10] < "2016-12-16":
#            done = True # the fetching works backwards, so we break if we hit 2016
#            break
#        all_releases.append({
#            "tag": rel["tag_name"], #github tag name
#            "date": rel["published_at"][:10] # just pull date (first 10 chars)
#        })
#
#    pg += 1
#    time.sleep(2)
#
#print(f"Latest release: {all_releases[0]['date']}")
#print(f"Oldest release: {all_releases[-1]['date']}")
#print(f"Number of releases: {len(all_releases)}")

# download each release and extract (block_id, route_long_name)
# loops thru all and skips if failed
#raw_gtfs_linkages = []
#for i, rel in enumerate(all_releases):
#    try:
#        linkage = get_train_line_linkage(rel["tag"], rel["date"])
#        if linkage is not None:
#            raw_gtfs_linkages.append(linkage)
#    except Exception as e:
#        print(f"Skipped {rel['tag']}: {e}")
#    if (i + 1) % 10 == 0:
#        print(f"[{i+1}/{len(all_releases)}] {rel['date']}")
#    time.sleep(0.5)

# Save raw downloads
#(pd.concat(raw_gtfs_linkages, ignore_index = True)
#           .astype({"block_id": str})
#           .to_parquet(f"{BASEPATH}/gtfs_linkages_since_2017_raw.parquet"))

# CLEAN FETCHED GTFS DATA
# Combine raw downloads, de-duplicate, rename columns
#gtfs = (
#    pd.concat(raw_gtfs_linkages, ignore_index = True) # combine
#    .drop_duplicates()                          # drop dups
#    .astype({"block_id": str})                  # train num -> str
#    .rename(columns = {"block_id": "train_number", # rename cols
#                       "route_long_name": "line"})
#)
#
#gtfs["gtfs_date"] = pd.to_datetime(gtfs["gtfs_date"])
#
# Save cleaned linkages
#gtfs.to_parquet(f"{BASEPATH}/gtfs_linkages_since_2017_clean.parquet")


# -----------------------------------------------------------------------
# Merge Train to Line by Date
# -----------------------------------------------------------------------
# Uses merge_asof to match each OTP observation to the most recent GTFS
# snapshot that predates it -- so a train that changed lines gets the right
# line for each date.
# Full merge on 20M+ rows crashes Colab, so we process one year at a time.

# Summary of GTFS link creation
print(f"{len(gtfs)} unique train-line-date matches in GTFS releases from:")
print(gtfs["gtfs_date"].min(), " to ", gtfs["gtfs_date"].max())

# How many unique train lines does individual train serve across our data?
print(gtfs.groupby("train_number")["line"].nunique().value_counts())

# We can see > 50% of our individual trains have operated on more than one
# line since 2017. Need to run date-based merge to match train to line as
# accurately as possible according to GTFS release.

# drop line column if it exists from earlier merge attempt
if "line" in df.columns:
    df = df.drop(columns = "line")

# sort on date
df = (df[df["service_date"].dt.year >= 2017] # filter to 2017 onwards
      .sort_values("service_date")
      .reset_index(drop = True))

gtfs["gtfs_date"] = pd.to_datetime(gtfs["gtfs_date"]) # convert to datetime
gtfs = (gtfs.sort_values("gtfs_date").reset_index(drop = True))

years = df["service_date"].dt.year.unique()
yrs = []


# join
for year in sorted(years):
    yr = df[df["service_date"].dt.year == year]
    linked = pd.merge_asof(
        yr,
        gtfs[["train_number", "gtfs_date", "line"]],
        left_on = "service_date",
        right_on = "gtfs_date",
        by = "train_number",
        direction = "backward"
    )
    yrs.append(linked)
    print(f"{year}: {linked['line'].notna().mean()*100:.1f}% matched")

df_linked = pd.concat(yrs, ignore_index = True)
matched = df_linked['line'].notna()

print(f"\nOverall - matched: {matched.sum():,} / {len(df_linked):,} ({matched.mean()*100:.1f}%)")

# original pull included ~3500 obs. which matched to non-RR lines
# it's a tiny amount of mismatch, but filtering them out here.
# filter to Regional Rail lines only (removes ~3500 non-rail rows from GTFS contamination)
rail_lines = [
    "Airport Line", "Chestnut Hill East Line", "Chestnut Hill West Line",
    "Cynwyd Line", "Fox Chase Line", "Lansdale/Doylestown Line",
    "Manayunk/Norristown Line", "Media/Elwyn Line", "Media/Wawa Line",
    "Paoli/Thorndale Line", "Trenton Line", "Warminster Line",
    "West Trenton Line", "Wilmington/Newark Line"
]
df_linked = df_linked[df_linked["line"].isin(rail_lines) | df_linked["line"].isna()]
print(f"Rows after filtering to Regional Rail matches ONLY: {len(df_linked):,}")

matched2 = df_linked['line'].notna()
print(f"Final - matched: {matched2.sum():,} / {len(df_linked):,} ({matched2.mean()*100:.1f}%)")


# -----------------------------------------------------------------------
# Save
# -----------------------------------------------------------------------
# Save merged dataframe, with lateness + train line names.
# Subsequent scripts load from here.

df_linked.to_parquet(f"{BASEPATH}/df_gtfs_linked.parquet")
print("Saved df_gtfs_linked.parquet")
