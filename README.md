# NYC Taxi Ingestion and ETL Pipeline (A2 + A3)

This repository contains coursework deliverables for CS-404 Big Data Analytics:

- Assignment 02: dataset justification, ingestion pipeline, profiling report.
- Assignment 03: PySpark ETL pipeline, analytics queries, charts, and final report.

## Project Files

- `ingest.py`: A2 automated ingestion pipeline (load, validate, upload to HDFS, logging).
- `profile_data.py`: A2 profiling report generator.
- `etl.py`: A3 PySpark ETL (transform, model, load, validate, optimize).
- `analytics.py`: A3 Spark SQL queries and visualization generation.
- `build_final_report.py`: Builds `final_report.pdf` from generated outputs.
- `requirements.txt`: Python dependencies.
- `Dataset_Justification.pdf`: A2 dataset rationale.
- `profiling_report.pdf`: A2 profiling baseline.
- `hdfs_screenshot.png`: HDFS directory screenshot evidence.

## Setup

1. Create a virtual environment:
   - PowerShell:
     - `python -m venv .venv`
     - `.venv\Scripts\Activate.ps1`
2. Install dependencies:
   - `pip install -r requirements.txt`

## Run Assignment 02

1. Ingestion:
   - `python ingest.py`
2. Profiling:
   - `python profile_data.py`

## Run Assignment 03 (HDFS-based, built on A2)

Default HDFS paths used by A3:

- Raw input (from A2): `hdfs:///warehouse/raw/nyc_taxi/year=2024/month=01`
- Processed output: `hdfs:///warehouse/processed/nyc_taxi`

Optional overrides:

- PowerShell:
  - `$env:A3_RAW_BASE="hdfs:///warehouse/raw/nyc_taxi/year=2024/month=01"`
  - `$env:A3_PROCESSED_BASE="hdfs:///warehouse/processed/nyc_taxi"`

Run sequence:

1. ETL (Spark + HDFS):
   - `spark-submit etl.py`
2. Analytics + charts (reads processed HDFS parquet):
   - `spark-submit analytics.py`
3. Final report build:
   - `python build_final_report.py`

Generated artifacts:

- Query outputs (`.csv`) in `outputs/`
- Charts (`.png`) in `outputs/`
- Final report: `final_report.pdf`

## Notes

- A3 scripts are configured to use HDFS URIs by default.
- `etl.py` generates `etl_summary.json`; `build_final_report.py` uses it for row count verification.

## Group Members

- Add group members here before submission.
