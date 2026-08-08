# SEPTA Regional Rail Reliability

An analysis of SEPTA Regional Rail on-time performance (OTP), built entirely from public data: SEPTA's
live delay feed ([archived by William Entriken](https://huggingface.co/datasets/fulldecent/septa-regionalrail-otp)),
historical [GTFS schedule releases](https://github.com/septadev/GTFS),
[ridership](https://opendataphilly.org/datasets/septa-ridership-statistics/) and calendar data, NOAA
weather observations (collected, cleaned, and compiled by Charlotte Mansfield in `weather/`), and
Amtrak/SEPTA track-ownership boundaries.

Two questions drive the analysis:

1. **Before departure**, how much of a train's eventual delay risk can be anticipated from information
   already available?
2. **During the run**, where and under what conditions does delay accumulate?

## Key findings

- Regional Rail delay is structured, not random. Recent operational history — a train's own recent runs,
  its line's recent performance, and network-wide conditions — carries far more pre-departure predictive
  signal than contemporaneous context like weather, holidays, sports schedules, or rush-hour timing.
- A tuned gradient-boosted classifier reaches **0.8045 ROC-AUC** and **0.610 late-class PR-AUC** on a
  held-out 2025 test year, identifying up to 73.2% of delayed runs at a more alert-sensitive threshold.
  That signal is real but incomplete: a meaningful share of delayed runs carry no detectable warning in
  the available pre-departure data.
- Delay accumulation during a run is direction- and distance-dependent: outbound trains lose roughly 5.9
  seconds per stop moving away from Center City, while inbound trains gain about 2.5 seconds per stop —
  the opposite direction.
- Amtrak-owned track segments run later than SEPTA-owned segments, and the effect concentrates almost
  entirely at the ownership boundary: segments where a train enters or exits Amtrak-owned track lose 5–7
  times as much time as segments fully inside either owner's territory.

## Repository structure

```
notebooks/    the analysis pipeline (Jupyter notebooks, run in numbered order)
data/         pipeline inputs and outputs (not tracked in git -- see Setup)
weather/      hourly weather data and prep script
report/       report figures
```

## Setup

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in a Census API key if running the Census-dependent steps
```

Notebooks read and write `data/` and `weather/` as siblings of `notebooks/`, using paths relative to the
notebook's own location. `data/` is not tracked in git; running the pipeline from `01` onward regenerates
it from public sources (SEPTA's delay feed and GTFS releases, live ridership/calendar/sports API fetches).
`weather/final_weather_ks.parquet` is tracked directly, since it's a static input rather than a
reproducible pipeline output.

## Pipeline

Run in numbered order — each stage reads outputs written by earlier stages.

| Stage | Notebook | Does |
|---|---|---|
| 01 | `01_otp_load.ipynb` | Loads and cleans the raw SEPTA delay feed |
| 02 | `02_gtfs_full_fetch.ipynb` | Fetches SEPTA's historical GTFS schedule releases |
| 02b | `02b_gtfs_merge.ipynb` | Resolves each delay observation's line, trip, direction, and scheduled time; writes the per-observation dataset the rest of the pipeline builds on |
| 02c | `02c_gtfs_stops_crosswalk.ipynb` | Builds a cross-release stop-ID crosswalk, correcting for SEPTA's stop renumbering over time |
| 03 | `03_EDA.ipynb` | Row-level exploratory analysis: lateness distribution, monthly OTP, time-of-day and line-level breakdowns |
| 04 | `04_ridership.ipynb` | Fetches and cleans SEPTA's public ridership data |
| 04b | `04b_ridership_safe.ipynb` | Builds a leak-free, trailing-safe ridership index used as a model feature |
| 05 | `05_calendar_events.ipynb` | Builds calendar/holiday and sports-schedule lookup tables, resolved to the specific line(s) each service exception affects |
| 06 | `06_weather_prep.ipynb` | Precomputes trailing weather aggregates anchored to scheduled departure time |
| 07 | `07_reduce_for_modeling.ipynb` | The core departure-safe feature reduction — builds the primary modeling dataset, one row per train run |
| 07b | `07b_checks.ipynb` | Validates the run-identification logic |
| 08 | `08_descriptive.ipynb` | Descriptive OTP analysis by year, line, and other dimensions |
| 09 | `09_prediction.ipynb` | Baseline model comparison: logistic regression, gradient boosting, random forest |
| 09b | `09b_stacked_feature.ipynb` | Builds a lateness-regression feature and tests stacking it into the classifier |
| 09c | `09c_tuning.ipynb` | Hyperparameter search |
| 09d | `09d_ensemble.ipynb` | Compares ensembling approaches against the tuned classifier alone |
| 09e | `09e_error_analysis.ipynb` | Calibration and error analysis of the final model |
| 10 | `10_feature_importance.ipynb` | Feature importance: grouped SHAP, permutation importance, late-class comparison, reduced-feature robustness |
| 11 | `11_spatial_delay_patterns.ipynb` | Distance-from-Center-City and Amtrak track-ownership regression across the full GTFS history |
| 12 | `12_figures.ipynb` | Consolidates report figures and tables |

## Report

The full write-up (methodology, results, and discussion) is assembled separately and isn't included in
this repository. `report/figures/` holds the figures the pipeline generates for it.
