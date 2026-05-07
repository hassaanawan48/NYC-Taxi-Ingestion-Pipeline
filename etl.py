#!/usr/bin/env python3
"""
Assignment 03 - PySpark ETL Pipeline

Builds directly on A2 HDFS-ingested raw data. Reads raw Parquet from HDFS,
transforms it, builds warehouse tables, writes processed Parquet back to HDFS,
and validates row-count/key-column quality checks.
"""

import logging
import os
import json
from pathlib import Path

import pandas as pd

try:
    from pyspark.sql import SparkSession
    from pyspark.sql import functions as F
    from pyspark.sql.window import Window
    PYSPARK_AVAILABLE = True
except Exception:
    PYSPARK_AVAILABLE = False


DATA_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-01.parquet"
RAW_FILE = "yellow_tripdata_2024-01.parquet"
# A3 must build on A2 HDFS output.
RAW_BASE = os.environ.get("A3_RAW_BASE", "hdfs:///warehouse/raw/nyc_taxi/year=2024/month=01")
PROCESSED_BASE = os.environ.get("A3_PROCESSED_BASE", "hdfs:///warehouse/processed/nyc_taxi")
RUN_MONTH = "2024-01"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("etl.log", encoding="utf-8")],
)
LOGGER = logging.getLogger("a3-etl")


def ensure_input_file() -> str:
    """Use local parquet if present; otherwise download it."""
    if Path(RAW_FILE).exists():
        LOGGER.info("Using local input file: %s", RAW_FILE)
        return RAW_FILE

    import requests

    LOGGER.info("Downloading input data from %s", DATA_URL)
    with requests.get(DATA_URL, stream=True, timeout=120) as response:
        response.raise_for_status()
        with open(RAW_FILE, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
    LOGGER.info("Downloaded %s", RAW_FILE)
    return RAW_FILE


def build_spark():
    return (
        SparkSession.builder.appName("A3-NYC-Taxi-ETL")
        .config("spark.sql.shuffle.partitions", "16")
        .config("spark.sql.sources.partitionOverwriteMode", "dynamic")
        .getOrCreate()
    )


def transform_data(df):
    """
    Apply cleaning + derivations.
    Important: inline comments explicitly trace each transformation to A2 findings.
    """
    # A2 issue: duplicates were identified and inflate aggregates -> remove exact duplicates.
    cleaned = df.dropDuplicates()

    # A2 issue: datetime formatting inconsistencies -> standardize timestamps and derive trip duration.
    cleaned = (
        cleaned.withColumn("pickup_ts", F.to_timestamp("tpep_pickup_datetime"))
        .withColumn("dropoff_ts", F.to_timestamp("tpep_dropoff_datetime"))
        .withColumn(
            "trip_duration_minutes",
            (F.col("dropoff_ts").cast("long") - F.col("pickup_ts").cast("long")) / F.lit(60.0),
        )
    )

    # A2 issue: negative fare values -> replace with distance-band median fare.
    cleaned = cleaned.withColumn("distance_bin", F.round(F.col("trip_distance") * 2) / 2)
    medians = cleaned.groupBy("distance_bin").agg(F.expr("percentile_approx(fare_amount, 0.5)").alias("fare_med"))
    cleaned = (
        cleaned.join(F.broadcast(medians), on="distance_bin", how="left")
        .withColumn(
            "fare_amount_clean",
            F.when(F.col("fare_amount") < 0, F.col("fare_med")).otherwise(F.col("fare_amount")),
        )
        .drop("fare_med")
    )

    # A2 issue: passenger_count had zero/non-integer values -> round and enforce minimum 1.
    cleaned = cleaned.withColumn(
        "passenger_count_clean",
        F.greatest(F.lit(1), F.round(F.col("passenger_count")).cast("int")),
    )

    # A2 issue: tip greater than fare is logically inconsistent -> cap tip at cleaned fare.
    cleaned = cleaned.withColumn(
        "tip_amount_clean",
        F.when(F.col("tip_amount") > F.col("fare_amount_clean"), F.col("fare_amount_clean"))
        .otherwise(F.col("tip_amount"))
        .cast("double"),
    )

    # A2 issue: trip distance outliers -> cap distance at 99th percentile.
    p99_distance = cleaned.selectExpr("percentile_approx(trip_distance, 0.99) as p99").first()["p99"]
    cleaned = cleaned.withColumn(
        "trip_distance_clean",
        F.when(F.col("trip_distance") > F.lit(p99_distance), F.lit(p99_distance)).otherwise(F.col("trip_distance")),
    )

    # A2 issue: missing numeric values -> median imputation on key numeric fields.
    key_numeric = ["trip_distance_clean", "fare_amount_clean", "tip_amount_clean", "trip_duration_minutes"]
    median_row = cleaned.select(
        *[
            F.expr(f"percentile_approx({c}, 0.5)").alias(f"{c}_median")
            for c in key_numeric
        ]
    ).first()
    for c in key_numeric:
        cleaned = cleaned.withColumn(c, F.coalesce(F.col(c), F.lit(median_row[f"{c}_median"])))

    # A2 issue: missing categorical values -> mode-style fallback.
    cleaned = cleaned.withColumn("payment_type", F.coalesce(F.col("payment_type"), F.lit(0)))
    cleaned = cleaned.withColumn("VendorID", F.coalesce(F.col("VendorID"), F.lit(0)))

    # A3 transform requirement: derive business-friendly columns.
    cleaned = (
        cleaned.withColumn("pickup_date", F.to_date("pickup_ts"))
        .withColumn("pickup_year", F.year("pickup_ts"))
        .withColumn("pickup_month", F.month("pickup_ts"))
        .withColumn("pickup_hour", F.hour("pickup_ts"))
        .withColumn("pickup_weekday", F.date_format("pickup_ts", "E"))
        .withColumn(
            "time_of_day",
            F.when((F.col("pickup_hour") >= 5) & (F.col("pickup_hour") < 12), F.lit("morning"))
            .when((F.col("pickup_hour") >= 12) & (F.col("pickup_hour") < 17), F.lit("afternoon"))
            .when((F.col("pickup_hour") >= 17) & (F.col("pickup_hour") < 22), F.lit("evening"))
            .otherwise(F.lit("night")),
        )
        .withColumn("total_revenue", F.col("fare_amount_clean") + F.col("tip_amount_clean"))
        .withColumn(
            "distance_bucket",
            F.when(F.col("trip_distance_clean") < 2, F.lit("short"))
            .when(F.col("trip_distance_clean") < 7, F.lit("medium"))
            .otherwise(F.lit("long")),
        )
    )

    return cleaned


def build_dimensions_and_fact(df):
    # Caching optimization: reused for multiple dimension/fact creations.
    df.cache()
    _ = df.count()

    dim_datetime = (
        df.select("pickup_date", "pickup_year", "pickup_month", "pickup_hour", "pickup_weekday", "time_of_day")
        .dropna(subset=["pickup_date"])
        .dropDuplicates()
        .withColumn("datetime_key", F.row_number().over(Window.orderBy("pickup_date", "pickup_hour")))
    )

    dim_payment = (
        df.select("payment_type")
        .dropDuplicates()
        .withColumn(
            "payment_desc",
            F.when(F.col("payment_type") == 1, F.lit("Credit card"))
            .when(F.col("payment_type") == 2, F.lit("Cash"))
            .when(F.col("payment_type") == 3, F.lit("No charge"))
            .when(F.col("payment_type") == 4, F.lit("Dispute"))
            .when(F.col("payment_type") == 5, F.lit("Unknown"))
            .when(F.col("payment_type") == 6, F.lit("Voided"))
            .otherwise(F.lit("Other"))
        )
    )

    dim_trip_category = (
        df.select("distance_bucket")
        .dropDuplicates()
        .withColumn(
            "trip_category_desc",
            F.when(F.col("distance_bucket") == "short", F.lit("< 2 miles"))
            .when(F.col("distance_bucket") == "medium", F.lit("2 to < 7 miles"))
            .otherwise(F.lit(">= 7 miles")),
        )
    )

    fact_trips = (
        df.join(F.broadcast(dim_datetime), on=["pickup_date", "pickup_year", "pickup_month", "pickup_hour", "pickup_weekday", "time_of_day"], how="left")
        .join(F.broadcast(dim_payment), on=["payment_type"], how="left")
        .join(F.broadcast(dim_trip_category), on=["distance_bucket"], how="left")
        .select(
            "datetime_key",
            "payment_type",
            "distance_bucket",
            "VendorID",
            "passenger_count_clean",
            "trip_distance_clean",
            "trip_duration_minutes",
            "fare_amount_clean",
            "tip_amount_clean",
            "total_revenue",
            "pickup_year",
            "pickup_month",
            "pickup_date",
        )
    )

    return dim_datetime, dim_payment, dim_trip_category, fact_trips


def write_table(df, table_name: str, partition_cols):
    target = os.path.join(PROCESSED_BASE, table_name)
    LOGGER.info("Writing table %s to %s", table_name, target)
    writer = df.write.mode("overwrite").format("parquet")
    if partition_cols:
        writer = writer.partitionBy(*partition_cols)
    writer.save(target)
    return target


def validate_tables(tables):
    LOGGER.info("Running post-load validation")
    summary = {}
    for table_name, df, key_cols in tables:
        row_count = df.count()
        null_checks = {
            k: df.filter(F.col(k).isNull()).count()
            for k in key_cols
        }
        LOGGER.info("Table=%s rows=%d nulls_in_keys=%s", table_name, row_count, null_checks)
        summary[table_name] = {"row_count": row_count, "nulls_in_keys": null_checks}
    return summary


def run_pandas_etl():
    """
    Lightweight fallback for environments where PySpark is unavailable.
    Produces the same processed table layout in local Parquet files.
    """
    input_file = ensure_input_file()
    raw_df = pd.read_parquet(input_file)
    Path(RAW_BASE).mkdir(parents=True, exist_ok=True)
    raw_df.to_parquet(Path(RAW_BASE) / "raw.parquet", index=False)
    LOGGER.info("Raw row count (pandas fallback): %d", len(raw_df))

    # A2 issue: duplicates
    df = raw_df.drop_duplicates().copy()

    # A2 issue: datetime standardization
    df["pickup_ts"] = pd.to_datetime(df["tpep_pickup_datetime"], errors="coerce")
    df["dropoff_ts"] = pd.to_datetime(df["tpep_dropoff_datetime"], errors="coerce")
    df["trip_duration_minutes"] = (df["dropoff_ts"] - df["pickup_ts"]).dt.total_seconds() / 60.0

    # A2 issue: negative fare
    df["distance_bin"] = (df["trip_distance"] * 2).round() / 2
    fare_medians = df.groupby("distance_bin")["fare_amount"].median()
    df["fare_amount_clean"] = df.apply(
        lambda r: fare_medians.get(r["distance_bin"], r["fare_amount"]) if pd.notna(r["fare_amount"]) and r["fare_amount"] < 0 else r["fare_amount"],
        axis=1,
    )

    # A2 issue: passenger count clean
    df["passenger_count_clean"] = df["passenger_count"].fillna(1).round().clip(lower=1).astype(int)

    # A2 issue: tip greater than fare
    df["tip_amount_clean"] = df[["tip_amount", "fare_amount_clean"]].min(axis=1)

    # A2 issue: distance outliers
    p99 = df["trip_distance"].quantile(0.99)
    df["trip_distance_clean"] = df["trip_distance"].clip(upper=p99)

    # A2 issue: missing numeric values
    for col in ["trip_distance_clean", "fare_amount_clean", "tip_amount_clean", "trip_duration_minutes"]:
        df[col] = df[col].fillna(df[col].median())

    df["payment_type"] = df["payment_type"].fillna(0).astype(int)
    df["VendorID"] = df["VendorID"].fillna(0).astype(int)
    df["pickup_date"] = df["pickup_ts"].dt.date
    df["pickup_year"] = df["pickup_ts"].dt.year
    df["pickup_month"] = df["pickup_ts"].dt.month
    df["pickup_hour"] = df["pickup_ts"].dt.hour
    df["pickup_weekday"] = df["pickup_ts"].dt.day_name().str[:3]
    df["time_of_day"] = pd.cut(
        df["pickup_hour"],
        bins=[-1, 4, 11, 16, 21, 24],
        labels=["night", "morning", "afternoon", "evening", "night"],
        ordered=False,
    ).astype(str)
    df["total_revenue"] = df["fare_amount_clean"] + df["tip_amount_clean"]
    df["distance_bucket"] = pd.cut(
        df["trip_distance_clean"], bins=[-1, 2, 7, float("inf")], labels=["short", "medium", "long"]
    ).astype(str)

    dim_datetime = (
        df[["pickup_date", "pickup_year", "pickup_month", "pickup_hour", "pickup_weekday", "time_of_day"]]
        .dropna()
        .drop_duplicates()
        .sort_values(["pickup_date", "pickup_hour"])
        .reset_index(drop=True)
    )
    dim_datetime["datetime_key"] = dim_datetime.index + 1
    dim_payment = pd.DataFrame({"payment_type": sorted(df["payment_type"].dropna().unique())})
    payment_map = {1: "Credit card", 2: "Cash", 3: "No charge", 4: "Dispute", 5: "Unknown", 6: "Voided"}
    dim_payment["payment_desc"] = dim_payment["payment_type"].map(payment_map).fillna("Other")
    dim_trip_category = pd.DataFrame({"distance_bucket": ["short", "medium", "long"]})
    dim_trip_category["trip_category_desc"] = ["< 2 miles", "2 to < 7 miles", ">= 7 miles"]

    fact = df.merge(
        dim_datetime,
        on=["pickup_date", "pickup_year", "pickup_month", "pickup_hour", "pickup_weekday", "time_of_day"],
        how="left",
    )[
        [
            "datetime_key",
            "payment_type",
            "distance_bucket",
            "VendorID",
            "passenger_count_clean",
            "trip_distance_clean",
            "trip_duration_minutes",
            "fare_amount_clean",
            "tip_amount_clean",
            "total_revenue",
            "pickup_year",
            "pickup_month",
            "pickup_date",
        ]
    ]

    for name, out_df in {
        "dim_datetime": dim_datetime,
        "dim_payment": dim_payment,
        "dim_trip_category": dim_trip_category,
        "fact_trips": fact,
    }.items():
        out_path = Path(PROCESSED_BASE) / name
        out_path.mkdir(parents=True, exist_ok=True)
        out_df.to_parquet(out_path / "part-00000.parquet", index=False)
        LOGGER.info("Wrote %s rows=%d", name, len(out_df))


def main():
    if not PYSPARK_AVAILABLE:
        raise RuntimeError(
            "PySpark is required for Assignment 03 HDFS ETL. "
            "Please run in your HDFS/Spark-configured environment."
        )

    spark = build_spark()
    try:
        LOGGER.info("Reading A2 raw data from HDFS path: %s", RAW_BASE)
        raw_df = spark.read.parquet(RAW_BASE)
        LOGGER.info("Raw row count: %d", raw_df.count())

        transformed = transform_data(raw_df)
        dim_datetime, dim_payment, dim_trip_category, fact_trips = build_dimensions_and_fact(transformed)

        write_table(dim_datetime, "dim_datetime", ["pickup_year", "pickup_month"])
        write_table(dim_payment, "dim_payment", [])
        write_table(dim_trip_category, "dim_trip_category", [])
        write_table(fact_trips, "fact_trips", ["pickup_year", "pickup_month"])

        validation_summary = validate_tables(
            [
                ("dim_datetime", dim_datetime, ["datetime_key", "pickup_date"]),
                ("dim_payment", dim_payment, ["payment_type"]),
                ("dim_trip_category", dim_trip_category, ["distance_bucket"]),
                ("fact_trips", fact_trips, ["datetime_key", "payment_type", "distance_bucket"]),
            ]
        )
        validation_summary["raw"] = {"row_count": raw_df.count()}
        with open("etl_summary.json", "w", encoding="utf-8") as handle:
            json.dump(validation_summary, handle, indent=2)

        LOGGER.info("ETL completed for run month %s", RUN_MONTH)
        LOGGER.info("Processed tables written under: %s", PROCESSED_BASE)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
