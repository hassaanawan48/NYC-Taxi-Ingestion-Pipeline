#!/usr/bin/env python3
"""
ingest.py - Fully automated HDFS ingestion pipeline for NYC Taxi data.
Meets all Task 2 requirements: Load, Validate, Upload, Organize, Log.
"""

import os
import subprocess
import logging
import requests
import pandas as pd
from urllib.parse import urlparse

# ========== CONFIGURATION ==========
DATASET_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-01.parquet"
LOCAL_FILENAME = "yellow_tripdata_2024-01.parquet"
HDFS_BASE_DIR = "/warehouse/raw/nyc_taxi"
YEAR = "2024"
MONTH = "01"
HDFS_TARGET_DIR = f"{HDFS_BASE_DIR}/year={YEAR}/month={MONTH}"

# Hadoop command path (uses environment variable if set, otherwise default)
HADOOP_HOME = os.environ.get("HADOOP_HOME", r"D:\softwares\hadoop-3.3.6")
HDFS_BIN = "hdfs.cmd" if os.name == "nt" else "hdfs"
HDFS_CMD = os.path.join(HADOOP_HOME, "bin", HDFS_BIN)

# Logging setup: console and file
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("ingest.log")
    ]
)

# ========== 1. LOAD (download if not present) ==========
def load_dataset():
    """Download the dataset from source if not already present."""
    if os.path.exists(LOCAL_FILENAME):
        logging.info(f"File already exists locally: {LOCAL_FILENAME}")
        return True
    logging.info(f"Downloading from {DATASET_URL} ...")
    try:
        response = requests.get(DATASET_URL, stream=True)
        response.raise_for_status()
        total_size = int(response.headers.get('content-length', 0))
        with open(LOCAL_FILENAME, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        logging.info(f"Download complete: {LOCAL_FILENAME} ({total_size/(1024*1024):.2f} MB)")
        return True
    except Exception as e:
        logging.error(f"Download failed: {e}")
        return False

# ========== 2. VALIDATE ==========
def validate_dataset():
    """Perform pre-upload validation: file integrity, format, row count, column count."""
    logging.info(f"Validating {LOCAL_FILENAME}")

    # Check file exists
    if not os.path.isfile(LOCAL_FILENAME):
        logging.error("File not found")
        return False

    # File size
    size_bytes = os.path.getsize(LOCAL_FILENAME)
    size_mb = size_bytes / (1024 * 1024)
    logging.info(f"File size: {size_mb:.2f} MB")
    if size_bytes == 0:
        logging.error("File is empty")
        return False

    # Check file extension (format)
    if not LOCAL_FILENAME.endswith('.parquet'):
        logging.warning("File extension is not .parquet; expected Parquet format")
    else:
        logging.info("File format: Parquet (binary columnar)")

    # Encoding detection: Parquet is binary, not text; we note this in log
    logging.info("Encoding detection: Not applicable (Parquet is a binary format, no text encoding)")

    # Row count and column count using pandas
    try:
        df = pd.read_parquet(LOCAL_FILENAME)
        row_count = len(df)
        col_count = len(df.columns)
        logging.info(f"Row count: {row_count}")
        logging.info(f"Column count: {col_count}")

        if row_count < 500000:
            logging.warning(f"Row count ({row_count}) is below the required 500,000")
        else:
            logging.info("Row count validation passed (≥500,000)")

        if col_count < 8:
            logging.warning(f"Column count ({col_count}) is below the required 8")
        else:
            logging.info("Column count validation passed (≥8)")

        # Log a sample of data types
        dtypes = df.dtypes.astype(str).to_dict()
        logging.info(f"Data types (first 5): {list(dtypes.items())[:5]}")
        return True
    except Exception as e:
        logging.error(f"Validation failed while reading Parquet: {e}")
        return False

# ========== 3. UPLOAD & ORGANIZE ==========
def upload_to_hdfs():
    """Upload the validated dataset to HDFS with structured directory organization."""
    logging.info(f"Creating HDFS directory: {HDFS_TARGET_DIR}")
    mkdir_cmd = [HDFS_CMD, "dfs", "-mkdir", "-p", HDFS_TARGET_DIR]
    try:
        subprocess.run(mkdir_cmd, check=True, capture_output=True, text=True)
        logging.info(f"Directory created/verified: {HDFS_TARGET_DIR}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to create HDFS directory: {e.stderr}")
        return False

    logging.info(f"Uploading {LOCAL_FILENAME} to {HDFS_TARGET_DIR}")
    put_cmd = [HDFS_CMD, "dfs", "-put", "-f", LOCAL_FILENAME, HDFS_TARGET_DIR]
    try:
        result = subprocess.run(put_cmd, check=True, capture_output=True, text=True)
        logging.info("Upload successful")
        # Verify upload
        verify_cmd = [HDFS_CMD, "dfs", "-ls", f"{HDFS_TARGET_DIR}/{LOCAL_FILENAME}"]
        subprocess.run(verify_cmd, check=True, capture_output=True, text=True)
        logging.info(f"File confirmed in HDFS: {HDFS_TARGET_DIR}/{LOCAL_FILENAME}")
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"Upload failed: {e.stderr}")
        return False

# ========== MAIN PIPELINE ==========
def main():
    logging.info("="*50)
    logging.info("Starting HDFS Ingestion Pipeline for NYC Taxi Data")
    logging.info("="*50)

    # Step 1: Load
    if not load_dataset():
        logging.critical("Pipeline aborted: Load failed")
        return 1

    # Step 2: Validate
    if not validate_dataset():
        logging.critical("Pipeline aborted: Validation failed")
        return 1

    # Step 3: Upload & Organize
    if upload_to_hdfs():
        logging.info("Pipeline completed successfully.")
        return 0
    else:
        logging.critical("Pipeline aborted: Upload failed")
        return 1

if __name__ == "__main__":
    exit(main())