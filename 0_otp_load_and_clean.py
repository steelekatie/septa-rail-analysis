# 0: Load, Clean, and Save OTP Data
#
# Run this script once to load raw OTP data, clean it, and save locally.
# Subsequent scripts load the saved result -- no need to re-run this unless
# cleaning logic changes or we want to pull new updates.
#
# CONFIG: set BASEPATH to your local data directory before running
# -----------------------------------------------------------------------

BASEPATH = "data"  # local path to data directory (update as needed)

# -----------------------------------------------------------------------

# Library installs:
# datasets      : handles HuggingFace -> Dataset object
# pandas        : standard data analysis package
# numpy         : for scientific + math computation on large arrays
# matplotlib    : the basic plotting library in python
# seaborn       : wrapper to matplotlib - additional plotting capabilities

import logging
logging.getLogger("huggingface_hub").setLevel(logging.ERROR) # suppress HF warnings

from datasets import load_dataset
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import requests, zipfile, io
import os

os.makedirs(BASEPATH, exist_ok = True)


# -----------------------------------------------------------------------
# Load Data
# -----------------------------------------------------------------------
# Load the full train dataset (57 mb, 25mil rows) via HuggingFace directory
# split = "train" refers to HuggingFace dataset split name (not train/test)
#ds = load_dataset("fulldecent/septa-regionalrail-otp", split = "train")

# Save raw data
# otherwise, use read_parquet() below
#ds.to_parquet(f"{BASEPATH}/df_raw.parquet")

# Convert raw data to pandas
#df_orig = ds.to_pandas()

# Read saved raw OTP data ^ to pandas dataframe
df_orig = pd.read_parquet(f"{BASEPATH}/df_raw.parquet")


# -----------------------------------------------------------------------
# Clean Data
# -----------------------------------------------------------------------

# Remove rows w/ missing lateness vals, copy to new df for analysis.
# Can re-run from here to start over without needing to re-load the data
df = df_orig[df_orig["lateness"] != 999].copy()

# Init cleanup
# convert to date object
df["service_date"] = pd.to_datetime(df["service_date"])
df = df.sort_values(["service_date", "time"]).reset_index(drop = True)

# Septa's service day runs 3a -> 3a next day instead of midnight
# create additional column to correct calendar day if we ever need it
# though we prob want to keep to Septa standards in most cases
df["datetime"] = pd.to_datetime(df["service_date"].astype(str) + " " + df["time"])
mask = df["time"] < "03:00:00"
df.loc[mask, "datetime"] += pd.Timedelta(days = 1)

# Duplicate check
print("Duplicate rows:", df.duplicated().sum())
print(df[df.duplicated(keep = False)]
        .sort_values(["service_date", "time", "train_number"])
        .head(20))

# Look into specific days we see duplicates
print(df[df.duplicated()]["service_date"]
        .value_counts()
        .sort_index())
# Duplicated entries appear to all be from New Years Day in 2012, 2013, 2014, 2017
# Maybe API issues -- only 12k rows so I'll drop

df = df.drop_duplicates().reset_index(drop=True)

print("Null values:")
print(df.isnull().sum()) # none

print("Lateness dist:")
print(df["lateness"].describe().apply(lambda x: f"{x:.2f}")) # late distribution
# Looking into crazy max value: view delays over 3h
print("\n Trains with delays > 3 hours:")
print(df[df["lateness"] > 180])

# We see ~1200 rows with what seem to be actual lateness values > 3h.
# Interestingly they don't actually appear to be data issues -- we can see
# the same trains on the same dates with climbing delays tracked in real time.
# Our extreme values seem legit.
# Modeling should prob be done on log-transformed data given the extreme right
# tail here. Otherwise might want to convert to binary on time / late.

# Plotting distribution of delays
fig, (ax1, ax2) = plt.subplots(2, 1, figsize = (10, 8))

# Base scale, capped at 60m, where we capture most of the values
df[df["lateness"] <= 60]["lateness"].hist(
    bins = 60, ax = ax1, color = "steelblue", edgecolor = "white")
ax1.set_title("Lateness distribution (0–60 min)")
ax1.set_xlabel("Minutes late")
ax1.set_ylabel("Observations")

# Log scale, where we can see some of the extreme values
df["lateness"].hist(
    bins = 100, ax = ax2, color = "steelblue", edgecolor = "white")
ax2.set_yscale("log")
ax2.set_title("Full distribution (log scale)")
ax2.set_xlabel("Minutes late")
ax2.set_ylabel("Observations (log scale)")

plt.tight_layout()
plt.savefig(f"{BASEPATH}/lateness_distribution.png", dpi = 150)
plt.show()

# Create vars for year, month, day of week
df["year"] = df["service_date"].dt.year
df["month"] = df["service_date"].dt.month
df["day_of_week"] = df["service_date"].dt.day_name()

monthly_counts = (df.groupby(["year", "month"])
                     .size()
                     .reset_index(name = "count"))

monthly_counts["service_date"] = pd.to_datetime(
    monthly_counts[["year", "month"]]
    .assign(day = 1)) # assign all to day 1 of month (for viz purposes)

years = monthly_counts[monthly_counts["month"] == 1]["service_date"]

fig, ax = plt.subplots(figsize = (14, 4))
ax.bar(monthly_counts["service_date"], monthly_counts["count"],
       width = 20, color = "steelblue", edgecolor = "none")

ax.set_title("Monthly observation counts (data coverage)")
ax.set_ylabel("Observations")
ax.set_xlabel("")
ax.set_xticks(years)
ax.set_xticklabels([str(y.year) for y in years], rotation = 0)

plt.tight_layout()
plt.savefig(f"{BASEPATH}/coverage_all_years.png", dpi = 150)
plt.show()

# We see some major gaps in coverage.
# Note: GTFS data releases (which we'll use to match train number -> train line)
# begin in 2015, and we can see here that OTP data was not collected for most of
# 2014, 15, and 16 -- so we choose to restrict our window of observation to
# 2017 onwards and drop all data before then.

# Restrict to 2017 on
df = df[df["year"] > 2016]

# Investigating further gaps (2018/19/20)
monthly_counts = (df.groupby(["year", "month"])
                    .size()
                    .reset_index(name = "count"))
monthly_counts["service_date"] = pd.to_datetime(
    monthly_counts[["year", "month"]]
    .assign(day = 1)) # assign all to day 1 of month (for viz purposes)

# find missing months
full_range = pd.date_range(
    monthly_counts["service_date"].min(),
    monthly_counts["service_date"].max(),
    freq = "MS"
)
# never appearing
missing = full_range[~full_range.isin(monthly_counts["service_date"])]
print(f"Missing months: {missing.strftime('%Y-%m').tolist()}")

# flag low coverage months (below 5th pctile)
threshold = monthly_counts["count"].quantile(0.05)
low = monthly_counts[monthly_counts["count"] < threshold]

print(f"\nLow coverage months (below 5th percentile, {threshold:,.0f} obs):")
print(low[["service_date", "count"]].to_string(index = False))

low_months = set(low["service_date"])
mon = df["service_date"].dt.to_period("M").dt.to_timestamp()

# standard coverage - can filter only to these later on
df["period_flag"] = "normal"

# low coverage
df.loc[mon.isin(low_months), "period_flag"] = "low"

# Note: we have no rows from Aug 2018 thru Nov 2018
#                        or Nov 2019 thru Jan 2020


# -----------------------------------------------------------------------
# Save Cleaned Data
# -----------------------------------------------------------------------
# Save cleaned df. Subsequent scripts load from here.

df.to_parquet(f"{BASEPATH}/df_clean.parquet")
print("Cleaned data saved.")
print(f"Rows: {len(df):,}")
