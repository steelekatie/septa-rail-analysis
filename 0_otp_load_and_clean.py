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
# matplotlib    : used for coverage diagnostic plot

import logging
logging.getLogger("huggingface_hub").setLevel(logging.ERROR) # suppress HF warnings

from datasets import load_dataset
import pandas as pd
import matplotlib.pyplot as plt
import os

os.makedirs(BASEPATH, exist_ok = True)


# -----------------------------------------------------------------------
# Load Data
# -----------------------------------------------------------------------
# Downloads from HuggingFace on first run (~57 MB, 25M rows) and saves locally.
# On subsequent runs, loads from the saved parquet file automatically.
# split = "train" refers to the HuggingFace dataset split name (not train/test)

raw_path = f"{BASEPATH}/df_raw.parquet"

if not os.path.exists(raw_path):
    print("Downloading data from HuggingFace (~57 MB, 25M rows)...")
    ds = load_dataset("fulldecent/septa-regionalrail-otp", split = "train")
    ds.to_parquet(raw_path) # save to disk for future runs
    df_orig = ds.to_pandas() # convert to pandas for cleaning
    print("Download complete. Raw data saved to disk.")

else:
    print("Raw parquet found — loading from disk...")
    df_orig = pd.read_parquet(raw_path)


# -----------------------------------------------------------------------
# Clean Data
# -----------------------------------------------------------------------

# Remove rows w/ missing lateness vals, copy to new df for cleaning.
df = df_orig[df_orig["lateness"] != 999].copy()

# Init cleanup
# convert to date object, sort
df["service_date"] = pd.to_datetime(df["service_date"])
df = df.sort_values(["service_date", "time"]).reset_index(drop = True)

# # Duplicate check
# print("Duplicate rows:", df.duplicated().sum())
# print(df[df.duplicated(keep = False)]
#         .sort_values(["service_date", "time", "train_number"])
#         .head(20))

# # Look into specific days we see duplicates
# print(df[df.duplicated()]["service_date"]
#         .value_counts()
#         .sort_index())

# # Duplicated entries appear to all be from New Years Day in 2012, 2013, 2014, 2017
# # Maybe API issues -- only 12k rows so I'll drop

df = df.drop_duplicates().reset_index(drop=True)

print("Null values:")
print(df.isnull().sum()) # none

# Create vars for year, month, day of week
df["year"] = df["service_date"].dt.year
df["month"] = df["service_date"].dt.month
df["day_of_week"] = df["service_date"].dt.day_name()

# Plot monthly coverage across all years to check for gaps.
# Major gaps pre-2017 inform the filter below.
monthly_counts_all = (df.groupby(["year", "month"])
                        .size()
                        .reset_index(name = "count"))
monthly_counts_all["service_date"] = pd.to_datetime(
    monthly_counts_all[["year", "month"]]
    .assign(day = 1))

years_all = monthly_counts_all[monthly_counts_all["month"] == 1]["service_date"]

fig, ax = plt.subplots(figsize = (14, 4))
ax.bar(monthly_counts_all["service_date"], monthly_counts_all["count"],
       width = 20, color = "steelblue", edgecolor = "none")
ax.set_title("Monthly observation counts (data coverage, all years)")
ax.set_ylabel("Observations")
ax.set_xlabel("")
ax.set_xticks(years_all)
ax.set_xticklabels([str(y.year) for y in years_all], rotation = 0)
plt.tight_layout()
plt.savefig(f"{BASEPATH}/coverage_all_years.png", dpi = 150)
plt.show()

# GTFS data begins in 2015; OTP data has major gaps through 2016.
# Restrict to 2017+ for consistent coverage.
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
