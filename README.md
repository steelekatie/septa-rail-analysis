# SEPTA Regional Rail Analysis

Analysis of SEPTA Regional Rail on-time performance (OTP) using historical data from [fulldecent's HuggingFace dataset](https://huggingface.co/datasets/fulldecent/septa-regionalrail-otp) and historical GTFS releases from [SEPTA's GitHub](https://github.com/septadev/GTFS).

## Scripts

| File | Description |
|------|-------------|
| `0_otp_load_and_clean.py` | Load raw OTP data from HuggingFace (or local parquet), clean, and save `df_clean.parquet` |
| `1_gtfs_merge.py` | Fetch historical GTFS train-to-line linkages and merge with OTP data by date; saves `df_gtfs_linked.parquet` |
| `2_eda.ipynb` | Exploratory analysis of merged dataset: lateness distribution, OTP over time, train-level and line-level breakdowns |

## Data

Scripts expect a `data/` directory (set via `BASEPATH`). Key files:

- `data/df_raw.parquet` — raw OTP data (download from HuggingFace or generate via script)
- `data/df_clean.parquet` — output of script 0
- `data/gtfs_linkages_since_2017_clean.parquet` — historical GTFS train→line linkages
- `data/df_gtfs_linked.parquet` — output of script 1 (OTP + line names merged)

Data files are excluded from version control (see `.gitignore`).

## Dependencies

```
datasets
pandas
numpy
matplotlib
seaborn
requests
```

Install with: `pip install datasets pandas numpy matplotlib seaborn requests`
