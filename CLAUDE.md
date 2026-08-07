# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Sibling project to `DATS_5990_Independent_Study` (GWU DATS 5990 independent study, SEPTA Regional Rail on-time performance). That project's pipeline builds two frameworks: an *explanatory* one using each run's full observed trajectory (weather throughout the trip, lagged-outcome features, etc.), and a *departure-safe* one (`13_departure_prediction.ipynb`) that predicts terminal OTP using **zero information about the run in progress** — every feature is anchored to `sched_origin_sec` (scheduled departure): schedule structure, calendar, weather-as-of-scheduled-departure, and lagged operational history only.

This project promotes that departure-safe framing to the **primary** pipeline. It is a deliberate experiment, built in an isolated sibling project (its own git repo) so it can be developed and validated without touching the original project or its own in-progress manual revision pass. If it works out, the intent is to fully replace `DATS_5990_Independent_Study`'s primary structure with this one.

## Data strategy — self-contained (migrated 2026-07-28)

This project no longer reads anything live from `DATS_5990_Independent_Study`. It started as a
sibling-repo-reuse setup (`EXTERNAL_BASEPATH` pointing at the original project's `data/`), then was
migrated to fully stand alone once the departure-safe framing was confirmed as the permanent structure.
Every notebook now reads and writes only this project's own `data/` (`BASEPATH = "../data"`) and top-level
`weather/` directories.

Provenance of what's here:
- **Raw-ingestion notebooks ported in and executed**: `01_otp_load.ipynb`, `02_gtfs_full_fetch.ipynb`
  (new), `02b_gtfs_merge.ipynb` (renamed from the old `02_gtfs_merge.ipynb` stub), `04_ridership.ipynb`,
  `05_calendar_events.ipynb` — all ported from the original project's equivalents, adjusted only to drop
  `EXTERNAL_BASEPATH` (their own code already used a plain local `BASEPATH`, no other changes needed).
  `01`/`02b`/`04`/`05` were actually executed here against copied-in bootstrap inputs or live fetches (see
  Architecture table); `02_gtfs_full_fetch.ipynb` was ported and smoke-tested (a couple of live GitHub
  API/release-zip calls) but not run at full scale — its 4 checkpoint outputs were copied in directly
  instead, since re-running the full ~35-45 min bootstrap for data already on disk would be pure waste.
  `04_ridership.ipynb` fetches its own raw per-station and system-wide monthly ridership data live (both
  public ArcGIS Hub CSV downloads, no API key) rather than reading a pre-computed copy — ported 2026-07-28
  after an initial version of this migration left it reading a copied `4_ridership_by_line_month.parquet`,
  which didn't actually make the project self-contained (it was still just displaying/rescaling someone
  else's already-computed output). Verified the self-built base reproduces identical figures to what the
  copied file had (same YTD ratios, same Airport 2025 values, same 1,404-row output, same missingness).
  Split into `04`/`04b` the same day — see the Architecture table.
- **Static reference files copied, not re-derived**: `weather/final_weather_ks.parquet` (consumed by
  `06_weather_prep.ipynb`) and `weather/3_weather_explore_ks.r` (reference/provenance only — never
  executed here, see Environment below). Nothing else in `data/` is a copied file anymore — everything else
  is either produced by a ported notebook or built fresh (`4_ridership_by_line_month_safe.parquet`,
  `6_weather_prepped.parquet`, `7_df_departure_ready.parquet`).
- **Deliberately never ported**: the original project's census/Amtrak merge notebooks and its old
  `09`-`13` explanatory-modeling pipeline — both already ruled out in Gotchas below (pure functions of
  `line`; superseded by this project's own `07`-`10` pipeline).
- **`03_EDA.ipynb` ported and executed 2026-07-29** — after starting as a placeholder, migrated in from the
  original project's `03_EDA.ipynb` almost unchanged (lateness distribution, monthly OTP, time-of-day,
  line-level breakdowns all reproduce closely against this project's own `2_df_gtfs_linked.parquet`). The
  one section dropped rather than ported: the original's `train_number`-level breakdown (top-10
  latest/most-punctual trains, then a monthly-trend drilldown on the single most-observed `train_number`).
  `train_number` is actually GTFS's `block_id` (see `02b_gtfs_merge.ipynb`'s through-running fix) — grouping
  by it silently mixes distinct lines whenever a physical train continues through Center City onto a
  different line with no layover. Confirmed against this project's data before dropping it: 45.9% of all
  `train_number` values are ever seen on >1 `line`, 54.2% of `(train_number, service_date)` combos span 2
  lines in a day, and the original drilldown's own subject (`train_number` 514, the most-observed value)
  is exactly such a case — 50,497 of its pings are `Lansdale/Doylestown`, 29,713 are `Paoli/Thorndale`. The
  ported notebook's own markdown documents this in place of that section rather than silently omitting it.

Porting `05_calendar_events.ipynb` and actually running it here also fixed a real bug: the original
project's copy of `5_calendar_by_date.parquet` still reflected a dedup bug (`keep="first"` release instead
of `keep="last"`) that was fixed in that notebook's *code* on 2026-07-27 but never re-run there. Running
the ported notebook here produced the first-ever corrected version of that file — confirmed to differ from
the original's stale cache in exactly the one date the fix's own comment predicted (2021-12-25,
`service_id=M3`: `service_additions`/`service_removals` flip from (2,1) to (1,2)), nothing broader.

## Architecture: the notebook pipeline

`notebooks/` is numbered the same way as the original project (required run order, not writing order).
`01`/`02`/`02b`/`03`/`04`/`05` are real, executable logic ported in from the original project (see Data
strategy above). `04b` is this project's own addition, not present in the original (departure-safe-specific
leak-fixing, split out from `04` 2026-07-28).

**Pipeline rebuild, 2026-08-02.** `09b`-`09e` were reordered so hyperparameter tuning happens *before*
ensembling instead of after — the original ordering let `09c`'s ensemble weights get silently invalidated
by a later hyperparameter pass, since the weights were tuned against default-hyperparameter base learners
that then changed underneath them. Three files were renamed to keep the `09` letter matching actual run
order (see Gotchas); `09b` kept its filename but was repurposed. The finalized feature set gained
`stops_from_cc`, `any_service_exception`, and a stacked lateness-regression feature (`pred_lateness_gbm`);
`05`'s calendar-exception handling was corrected to resolve line-specific exceptions instead of treating
everything as system-wide; and the final model turned out to be tuned XGBoost alone, not an ensemble (see
Gotchas). `10`/`10b` were rebuilt around that simpler final model, and a new `10c` adds a late-class-specific
deep dive.

**`09e`/`10`-series restructuring, 2026-08-07.** A cell-by-cell audit of what each notebook cell actually
backs in `report_user_final.md`'s main text found real dead weight: sections computed but never cited
(main text or the handful of kept appendix tables), and near-duplicate compute across the `10`-series
files. `09e_error_analysis.ipynb` dropped its operating-threshold-selection section (the report only
notes thresholds are adjustable, not which one was chosen), its network-disruption-robustness section,
and its worst-confidently-wrong-predictions section, renumbering what's left. The five separate `10`-series
notebooks (`10`, `10b_shap_analysis.ipynb`, `10c_late_class_analysis.ipynb`,
`10d_reg_gbm_importance.ipynb`, `10e_reduced_features.ipynb`) were consolidated into a single
`10_feature_importance.ipynb`, dropping the old `10`'s classifier-level permutation importance
(superseded by `10b`'s grouped SHAP table), `10b`'s ungrouped SHAP ranking and beeswarm plot (uncited),
`10c`'s SHAP-restricted-to-late-class and recall-focused-permutation-importance sections (appendix-prose
only), and `10e`'s intermediate 55-feature cut (kept only the top-40 comparison). Every surviving number
was re-verified to reproduce exactly against the pre-consolidation notebooks' own cached output before the
old files were deleted.

**`11`-series consolidation, 2026-08-07.** The original `11_spatial_delay_patterns.ipynb` (distance-from-
Center-City delay accumulation, single most-recent GTFS release) was deleted — its own findings were never
cited in the report, and after `07`'s stop-position derivation was inlined the same day, it no longer fed
anything else in the pipeline either. `11d_1_demographic_check.ipynb` was also deleted, despite backing
Appendix C.5's kept demographic-regression table — that table's numbers stay in the report text as
written, but no longer trace back to a script in this repo. `11b_1_amtrak_ownership.ipynb` (full
2017-2025 GTFS history, Amtrak-ownership + distance regression) was renamed to take over the vacated `11`
slot, since it already covers the same distance-from-Center-City ground as a secondary finding alongside
its Amtrak-ownership headline. Its stop-position derivation was also restyled to match `07`'s: raw
per-direction index, then Inbound flipped onto the same 0-at-trunk scale as Outbound, instead of the old
version's Outbound-only lookup reused for both directions. Re-verified numerically identical (to 3-4
decimal places) against the pre-restyle run — the one small difference (`n=399` hops -> `n=400`) is a
genuine fix: one stop that Outbound's canonical trip skips but Inbound's doesn't previously had no
resolved position and got dropped, now resolves via Inbound's flipped index instead. Output files renamed
`11b_1_*` -> `11_*` to match; `12_figures.ipynb` updated accordingly.

| Stage | Notebook | Does |
|---|---|---|
| 01 | `01_otp_load.ipynb` | One-time load/clean of raw OTP data (`data/1_df_raw.parquet` -> `data/1_df_clean.parquet`) — ported from the original project, executed here |
| 02 | `02_gtfs_full_fetch.ipynb` | Bootstrap/refresh: fetches everything the pipeline needs from SEPTA's historical GTFS releases (~325 releases, one download per release, ~35-45 min). Ported but not run at full scale here — its 4 checkpoint outputs were copied in directly instead (see Data strategy) |
| 02b | `02b_gtfs_merge.ipynb` | Resolves each OTP ping's `line`/`trip_id`/`direction_id`/scheduled-time features via timing-based through-running disambiguation, using `01`'s and `02`'s outputs. Writes `data/2_df_gtfs_linked.parquet` — the per-ping resolved data this whole project is built on. Ported from the original project, executed here (reproduced its exact 99.3% match rate and train-420 two-line spot-check) |
| 03 | `03_EDA.ipynb` | Row-level EDA on `2_df_gtfs_linked.parquet` — lateness distribution, monthly OTP, time-of-day, line-level breakdowns. Ported from the original project and executed here 2026-07-29; its `train_number`-level section was dropped rather than ported (see Data strategy). Departure-specific descriptive analysis is still `08_descriptive.ipynb` |
| 04 | `04_ridership.ipynb` | Raw construction, generic/stable: fetches SEPTA's public per-station and system-wide monthly ridership datasets live, cleans/remaps line names, imputes missing years (2018/2020/2021/2025) — the same construction the original project's own `04_ridership.ipynb` does, ported here rather than read from a copied output. Writes `4_ridership_annual_by_line.parquet` and `4_ridership_monthly_index_fallback.parquet` for `04b` |
| 04b | `04b_ridership_safe.ipynb` | Departure-safe leak-fixing, split from `04` 2026-07-28 since this half is project-specific and more likely to be revisited independently: builds `4_ridership_by_line_month_safe.parquet` (the feature `07` merges in) via a trailing-safe monthly index and a YTD-ratio-scaled 2025 base, replacing three within/same-year normalization leaks the original project's equivalent feature had (see the notebook's own markdown) |
| 05 | `05_calendar_events.ipynb` | Builds per-date calendar/holiday (`5_calendar_by_date.parquet`) and sports home-game (`5_home_games.parquet`) lookup tables from `02`'s GTFS calendar checkpoint plus live MLB/NHL/ESPN API fetches. Ported from the original project, executed here — includes the `keep="last"` dedup fix (see Data strategy), producing the first-ever corrected version of `5_calendar_by_date.parquet`. **Revisited 2026-08-02**: resolves each exception's `service_id` to the specific line(s) it actually affects via the GTFS crosswalk (exact-release join against `2_gtfs_linkages_since_2017_clean.parquet`) instead of treating every exception as system-wide — ~20% of exception dates turned out to be genuinely line-specific, concentrated in late 2025 (planned track work reads more likely than the "SEPTA just files more now" explanation this originally carried). New output: `5_calendar_by_date_line.parquet`, merged onto `07`'s table by `(service_date, line)` alongside the original system-wide table |
| 06 | `06_weather_prep.ipynb` | **Not a row-level merge** (the departure-safe reduction never needs weather *during* a trip, only as-of-and-before scheduled departure) — loads the raw hourly weather table (`weather/final_weather_ks.parquet`) and precomputes trailing-window aggregates (`asof`/`3h`/`6h`/`24h`), writes `data/6_weather_prepped.parquet` |
| 07 | `07_reduce_for_modeling.ipynb` | The departure-safe reduction. Builds `run_id` locally, then per-run features: scheduled-window peak/sports overlap, departure-anchored weather (via `06`'s prepped table), expanded lagged-lateness features, deferred ridership/calendar merges. No Amtrak or census merge — both dropped as redundant with `line` (see Gotchas). Writes `data/7_df_departure_ready.parquet`. **Extended 2026-08-02**: adds `stops_from_cc` (ordinal distance from the Center City trunk; ~94% coverage, see Gotchas) and `any_service_exception`; merges `05`'s new line-specific exception table alongside the system-wide one. **`stops_from_cc` derivation inlined 2026-08-07**: originally read `11_line_direction_stops.parquet`
(produced by the notebook that used to occupy this `11` slot), which meant `07` silently depended on it
having already run despite the numbering implying the opposite — `07` now derives the same canonical
per-line-direction stop order itself (duplicated logic rather than a shared `utils.py` helper) so it no
longer reads anything from `11`. The current `11_spatial_delay_patterns.ipynb` (renamed from
`11b_1_amtrak_ownership.ipynb` the same day) was restyled to use this identical derivation approach |
| 02c | `02c_gtfs_stops_crosswalk.ipynb` | Cross-release `stop_id` crosswalk: SEPTA renumbers `stop_id`s across some historical GTFS releases and `stop_name` strings drift cosmetically even when the id doesn't change. Two-tier match against the canonical (most recent) release — `stop_id` identity first (~92-100% of years, except a genuine 2022 bulk renumbering), normalized-name matching as fallback. Reads `02`'s `2_gtfs_stops.parquet`/`2_gtfs_linkages_since_2017_clean.parquet`, writes `data/2_stop_id_crosswalk.parquet` (only consumed by `11_spatial_delay_patterns.ipynb` — independent of `02b`, can run any time after `02`). Previously undocumented in this table — added 2026-08-07 after a full from-scratch pipeline run surfaced it as an existing but unlisted notebook |
| 11 | `11_spatial_delay_patterns.ipynb` | **Renamed from `11b_1_amtrak_ownership.ipynb`, 2026-08-07** (see the `11`-series consolidation note above). Track ownership + distance regression across the full 2017-2025 GTFS history, using `02c`'s cross-release stop-id crosswalk. Two findings: delay increases with distance from Center City and is much higher Outbound than Inbound; Amtrak-owned track runs later than SEPTA-owned track, also concentrated Outbound (distance and line fixed effects controlled). Feeds `12_figures.ipynb`'s Table 2 and Appendix C.2/C.3 figures |
| 07b | `07b_checks.ipynb` | Lightweight validation of the `run_id`/turnaround-gap assignment |
| 08 | `08_descriptive.ipynb` | Skeleton only, deliberately — structure mirrors the original project's `10_descriptive.ipynb`, content intentionally left for manual authorship |
| 09 | `09_prediction.ipynb` | Logistic-baseline + `HistGradientBoostingClassifier` + `RandomForestClassifier` at default settings, evaluated together — establishes tree-based methods beat linear. Rerun 2026-08-02 against the finalized feature set; headline GBM numbers moved from 0.844/0.793 to 0.858/0.810 accuracy/ROC-AUC purely from the refreshed data (this notebook's own code is unchanged) |
| 09b | `09b_tuning.ipynb` | **Repurposed 2026-08-02** (previously threshold tuning, now in `09e`). Feature engineering: builds + lightly tunes a `lateness`-regression `HistGradientBoostingRegressor`, then tests whether stacking its out-of-fold predicted lateness in as an extra classifier feature helps — confirmed a real, substantial gain (RF: +1.07pp ROC-AUC / +2.69pp PR-AUC-late on the go/no-go test), adopted as `pred_lateness_gbm` in the final feature set (see Gotchas for the leakage question and why it dominates downstream importance rankings) |
| 09c | `09c_tuning.ipynb` | **Repurposed + renamed 2026-08-02** (previously at the `09e` position; the old `09c` position's ensemble content moved to `09d`). Random hyperparameter search for XGBoost/RF on the finalized feature set (scored on `val`), plus a quick logistic-regression regularization check (confirms logit's weakness is functional form, not under-regularization). Stops at picking hyperparameters — does not touch ensemble weights |
| 09d | `09d_ensemble.ipynb` | **Repurposed + renamed 2026-08-02** (previously at the `09c` position; the old `09d` position's error-analysis content moved to `09e`). Blends tuned logit/XGBoost/RF three ways (equal-weight, tuned-weight grid search, logistic meta-learner) on `train_sub`/`val`/`test` — found the tuned-weight blend tied with XGBoost alone on every metric, including on `val` itself, so **the final model is tuned XGBoost alone**, not an ensemble (see Gotchas) |
| 09e | `09e_error_analysis.ipynb` | **Repurposed + renamed 2026-08-02** (previously at the `09d` position; absorbs `09b`'s old threshold-selection content). Rebuilt around tuned XGBoost alone: calibration + subgroup calibration (line/weather/pm_peak_x_sports), accuracy near the OTP boundary, Airport-line miss-pattern deep dive, seasonal (2024 vs. 2025) robustness. **Restructured 2026-08-07**: cut operating-threshold selection, network-disruption robustness, and worst-confidently-wrong-predictions (none cited in the report), renumbering the remaining 5 sections |
| 10 | `10_feature_importance.ipynb` | **Consolidated 2026-08-07** from five separate notebooks into one (see the restructuring note above). Four sections: (1) grouped SHAP ranking — additive attribution summed across correlated feature families, backs Appendix B.5; (2) regression-GBM permutation importance — decomposes `pred_lateness_gbm` into the base `GBM_FEATURES` it's built from (top driver: `lag_train_lateness_1run_mean`), backs Appendix B.9; (3) late-class SHAP comparing caught vs. missed delays, backs Appendix B.7/Section 3.6; (4) reduced-feature robustness — cutting to the top 40 features barely moves any metric. `pred_lateness_gbm` dominates permutation importance by ~60x but SHAP by a much more modest ~7x — see Gotchas for why |
| 12 | `12_figures.ipynb` | **New 2026-08-05.** Consolidates every report-specific figure/table (as opposed to each stage's own internal EDA output) into one place, each cell loading from a small cached parquet in `data/` rather than reloading the full dataset or refitting any model — runs end to end in well under a minute. Currently covers: Table A.1 (systemwide OTP by year, from `08`), the line-level OTP trend chart (from `08`), Appendix D.2 threshold sensitivity (from `08`), the grouped SHAP importance chart (from `10`), and a candidate ROC-curve-deltas-vs-final-model chart (from `09d`, not yet decided whether it goes in the report). The now-duplicated plotting code was removed from `08_descriptive.ipynb` and `10_feature_importance.ipynb` — those notebooks keep only the caching lines that produce this notebook's inputs |

## Gotchas

- **`is_amtrak_line` and every census demographic variable are pure deterministic functions of `line`** — confirmed empirically in the original project (2026-07-27): `is_amtrak_line` never varies within a `line`, and `6_census_line_demos.parquet` is literally one row per line. In a model that already includes `line` as a raw categorical feature, both add zero information a tree/logit can't already get from splitting on `line` directly — confirmed near-worthless by the original project's `12_prediction.ipynb` permutation importance (all near-zero once `line` is available), and `is_amtrak_line`'s redundancy was independently already recognized in that project's `11_logreg.ipynb` `drop_cols`. Neither is used as a feature anywhere in this project, and no Amtrak/census merge step exists in the primary pipeline at all (deferred to a future "if time" corridor/equity deep-dive instead — see the original project's `dats5990_corridor_delay_deep_dive` memory). `avg_daily_boards_monthly` is different (varies by `line`/year/month, not collinear with `line` alone, already shown to carry real standalone signal) and is kept.
- **Headless `jupyter nbconvert --execute` needs its kernel forced explicitly** — same root cause as the original project (`nbconvert` resolves to the base Anaconda install unless this venv's kernel, `dats5990-departure-venv`, is passed explicitly via `--ExecutePreprocessor.kernel_name=dats5990-departure-venv`). See the original project's `CLAUDE.md` Gotchas for the full mechanism.
- **`weather/final_weather_ks.parquet` (consumed by `06_weather_prep.ipynb`) was produced by an older R script than the most refined one available** — FYI/inherited from the original project, not fixed here. `weather/3_weather_explore_ks.r` (copied in for reference) is what actually produced this file, using a UTC-5 hack for date alignment. A later script in the original project, `4_weather_explore.r` (not copied here — out of scope), fixes that alignment to be properly DST-aware, but its output was never renamed/wired to the `_ks` filename actually consumed downstream. Regenerating weather data at all requires R (`tidyverse`/`arrow`) — not managed by this project's Python venv/`requirements.txt`.
- **`02_gtfs_full_fetch.ipynb` is ported but only smoke-tested here, not run at full scale** — its 4 checkpoint outputs were copied in from the original project instead (see Data strategy). The smoke test (a live GitHub releases-list call + one release-zip download/extract via `utils.fetch_gtfs_releases`/`fetch_gtfs_release_zip`) confirms the imports/URLs still resolve in this environment, but a full run (needed to pick up new GTFS releases in the future) has not been validated end-to-end in this project.
- **`stops_from_cc`'s ~94% coverage isn't cheaply fixable** — the canonical per-line-direction stop-position table `07` derives (a lighter-weight version of the same style `11_spatial_delay_patterns.ipynb` uses, minus that notebook's cross-release stop-id remapping) reflects only the single most recent GTFS release, while `origin_stop_id` in the modeling table is release-native across ~325 historical releases back to 2017. SEPTA stop-id drift over that window means ~5-6% of rows don't match and get `NaN` (concentrated on Warminster, Media/Wawa, West Trenton, Lansdale/Doylestown). Trees handle the resulting missingness natively. `02c_gtfs_stops_crosswalk.ipynb`'s cross-release crosswalk doesn't close this gap either — it corrects stop-id drift for canonical-release lookups, not the release-native `origin_stop_id` this join uses directly (see `98e_candidate_features.ipynb`'s notes for the original investigation).
- **`pred_lateness_gbm` (the stacked feature, `09b`) is not leakage, but its dominance in `10`'s permutation importance is partly definitional, not evidence of a uniquely powerful new signal** — it's trained to predict `log1p(lateness)`, the same continuous quantity `is_otp` thresholds, using only `GBM_FEATURES` (confirmed directly: neither `lateness` nor `is_otp` is in that list, so it can't smuggle in anything beyond what the classifier already has). Out-of-fold construction on `train_sub` (5-fold `KFold`, shuffled not chronological — reasoned through in `98c`'s original markdown: every `GBM_FEATURES` column is already row-level leak-safe regardless of which fold a row lands in), honest out-of-sample fit for `val`/`test`. `10`'s Section 4 permutation importance shows it dominating by ~60x; Section 1's SHAP ranking shows a much more modest ~7x gap, since the raw lag features it's built from still carry real independent attribution once the model has both — explain this distinction in any write-up that cites the ranking, not just the number.
- **The final model is tuned XGBoost alone, not an ensemble** (decided in `09d`, 2026-08-02) — the tuned-weight blend of logit/XGBoost/RF was tied with XGBoost alone on every metric, including on `val` itself, the exact data the weight search optimized against (0.8261 blend vs. 0.8259 XGBoost alone — a 0.0002 difference on a grid stepped in 0.05 increments). RF's ~10% weight isn't distinguishable from grid-search noise. Dropping it also removes the single largest compute cost downstream: RF's fit time (`max_features=None` in the tuned config took ~20 min to fit once) and its `TreeExplainer`'s ~1s/row SHAP cost. `09e`/`10` both reflect this — no blend weights, no RF anywhere in the current pipeline.
- **`shap.TreeExplainer` (v0.52.0) cannot correctly explain sklearn's `HistGradientBoostingRegressor`/`Classifier` when it uses native categorical splits** (`categorical_features="from_dtype"`, as `09b`/`09d`'s regression GBM does for `line`) — upstream tracked as shap GH #1028. It fails outright converting the categorical column to a numeric array (`X.to_numpy(dtype=...)` chokes on category strings like `"Lansdale/Doylestown"`), and even worked around, its tree-reconstruction code (`shap/explainers/_tree.py`) only extracts a plain numeric `threshold` per node and applies ordinary `<=` semantics — it does not reconstruct the categorical bitset test HistGradientBoosting actually uses, so results would be silently wrong rather than erroring. `10`'s SHAP sections (1 and 3) avoid this because they explain the final **XGBoost** classifier, which splits natively on categoricals in a way shap does support. `10`'s Section 2 hits this directly trying to explain the regression GBM and skips SHAP entirely (permutation importance only) rather than pay for a much slower model-agnostic (`Permutation`) explainer for no accuracy benefit over what permutation importance already gives cleanly.
- **Notebook renaming, 2026-08-02** — `09b`/`09c`/`09d`/`09e` were reordered so hyperparameter tuning happens before ensembling, not after (the original ordering let `09c`'s ensemble weights get silently invalidated by a later hyperparameter pass). Old `09c_ensemble.ipynb` → `09d_ensemble.ipynb`; old `09d_error_analysis.ipynb` → `09e_error_analysis.ipynb`; old `09e_hyperparam_tuning.ipynb` → `09c_tuning.ipynb`; `09b_tuning.ipynb` kept its filename but was repurposed from threshold-tuning to feature engineering. Any older reference (the original report draft, prior conversation history) describing `09c` as ensembling or `09e` as hyperparameter tuning is describing the pre-2026-08-02 structure.
