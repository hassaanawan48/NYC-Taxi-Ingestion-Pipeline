#!/usr/bin/env python3
"""
Assignment 03 - Spark SQL analytics and visualization.

Reads processed Parquet warehouse tables from HDFS, executes 5 business
queries, and produces 4 required chart types with interpretations.
"""

import os
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
try:
    from pyspark.sql import SparkSession
    PYSPARK_AVAILABLE = True
except Exception:
    PYSPARK_AVAILABLE = False


HDFS_DEFAULT_FS = os.environ.get("A3_HDFS_DEFAULTFS", "hdfs://localhost:9000")
PROCESSED_BASE = os.environ.get("A3_PROCESSED_BASE", "hdfs:///warehouse/processed/nyc_taxi")
OUTPUT_DIR = Path("outputs")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def spark_session():
    return (
        SparkSession.builder.appName("A3-NYC-Taxi-Analytics")
        .config("spark.hadoop.fs.defaultFS", HDFS_DEFAULT_FS)
        .getOrCreate()
    )


def run_queries(spark):
    fact_path = os.path.join(PROCESSED_BASE, "fact_trips")
    dim_datetime_path = os.path.join(PROCESSED_BASE, "dim_datetime")
    dim_payment_path = os.path.join(PROCESSED_BASE, "dim_payment")

    fact = spark.read.parquet(fact_path)
    dim_datetime = spark.read.parquet(dim_datetime_path)
    dim_payment = spark.read.parquet(dim_payment_path)

    fact.createOrReplaceTempView("fact_trips")
    dim_datetime.createOrReplaceTempView("dim_datetime")
    dim_payment.createOrReplaceTempView("dim_payment")

    queries = {
        "q1_hour_weekday_volume_fare": """
            -- Business Q1: How do trip volume and average fare vary by hour and day of week?
            SELECT
                d.pickup_weekday,
                d.pickup_hour,
                COUNT(*) AS trip_count,
                ROUND(AVG(f.fare_amount_clean), 2) AS avg_fare
            FROM fact_trips f
            JOIN dim_datetime d ON f.datetime_key = d.datetime_key
            GROUP BY d.pickup_weekday, d.pickup_hour
            ORDER BY
                CASE d.pickup_weekday
                    WHEN 'Mon' THEN 1
                    WHEN 'Tue' THEN 2
                    WHEN 'Wed' THEN 3
                    WHEN 'Thu' THEN 4
                    WHEN 'Fri' THEN 5
                    WHEN 'Sat' THEN 6
                    WHEN 'Sun' THEN 7
                    ELSE 8
                END,
                d.pickup_hour
        """,
        "q2_distance_fare_correlation": """
            -- Business Q2: What is the correlation between trip distance and fare amount?
            SELECT
                ROUND(f.trip_distance_clean, 1) AS distance_bin,
                COUNT(*) AS trip_count,
                ROUND(AVG(f.fare_amount_clean), 2) AS avg_fare,
                ROUND(CORR(f.trip_distance_clean, f.fare_amount_clean), 4) AS distance_fare_corr
            FROM fact_trips f
            GROUP BY ROUND(f.trip_distance_clean, 1)
            HAVING COUNT(*) >= 50
            ORDER BY distance_bin
        """,
        "q3_payment_preference_time_location": """
            -- Business Q3: How does payment type preference change by time and location?
            SELECT
                f.PULocationID,
                d.pickup_hour,
                p.payment_desc,
                COUNT(*) AS trip_count,
                ROUND(
                    100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY f.PULocationID, d.pickup_hour),
                    2
                ) AS payment_share_pct
            FROM fact_trips f
            JOIN dim_datetime d ON f.datetime_key = d.datetime_key
            JOIN dim_payment p ON f.payment_type = p.payment_type
            GROUP BY f.PULocationID, d.pickup_hour, p.payment_desc
            HAVING COUNT(*) >= 100
            ORDER BY trip_count DESC
            LIMIT 200
        """,
        "q4_avg_tip_by_segments": """
            -- Business Q4: How does average tip differ by payment type, distance, or vendor?
            SELECT
                p.payment_desc,
                f.distance_bucket,
                f.VendorID,
                COUNT(*) AS trip_count,
                ROUND(AVG(f.tip_amount_clean), 2) AS avg_tip,
                ROUND(AVG(f.tip_amount_clean / NULLIF(f.fare_amount_clean, 0)) * 100, 2) AS avg_tip_pct_of_fare
            FROM fact_trips f
            JOIN dim_payment p ON f.payment_type = p.payment_type
            GROUP BY p.payment_desc, f.distance_bucket, f.VendorID
            HAVING COUNT(*) >= 100
            ORDER BY avg_tip DESC
        """,
        "q5_top_location_pairs": """
            -- Business Q5: Which pickup/dropoff location pairs have highest volume and fare?
            SELECT
                f.PULocationID,
                f.DOLocationID,
                COUNT(*) AS trip_count,
                ROUND(AVG(f.fare_amount_clean), 2) AS avg_fare,
                ROUND(SUM(f.total_revenue), 2) AS total_revenue,
                DENSE_RANK() OVER (ORDER BY COUNT(*) DESC, AVG(f.fare_amount_clean) DESC) AS pair_rank
            FROM fact_trips f
            GROUP BY f.PULocationID, f.DOLocationID
            HAVING COUNT(*) >= 50
            ORDER BY pair_rank
            LIMIT 100
        """,
    }

    results = {}
    for name, sql in queries.items():
        pdf = spark.sql(sql).toPandas()
        pdf.to_csv(OUTPUT_DIR / f"{name}.csv", index=False)
        results[name] = pdf
    return results


def generate_visuals(results):
    sns.set_theme(style="whitegrid")

    # 1) Line chart
    q1 = results["q1_hour_weekday_volume_fare"].copy()
    q1["pickup_hour"] = q1["pickup_hour"].astype(int)
    top_days = (
        q1.groupby("pickup_weekday", as_index=False)["trip_count"]
        .sum()
        .sort_values("trip_count", ascending=False)
        .head(4)["pickup_weekday"]
        .tolist()
    )
    q1_top = q1[q1["pickup_weekday"].isin(top_days)]
    fig, ax = plt.subplots(figsize=(10, 5))
    sns.lineplot(data=q1_top, x="pickup_hour", y="trip_count", hue="pickup_weekday", marker="o", ax=ax)
    ax.set_title("Trip Volume by Hour (Top Weekdays)")
    ax.set_xlabel("Pickup Hour")
    ax.set_ylabel("Trip Count")
    plt.tight_layout()
    fig.savefig(OUTPUT_DIR / "chart_line_trend.png", dpi=150)
    plt.close(fig)

    # 2) Bar chart
    q5 = results["q5_top_location_pairs"].copy().head(10)
    q5["location_pair"] = q5["PULocationID"].astype(str) + "→" + q5["DOLocationID"].astype(str)
    fig, ax = plt.subplots(figsize=(10, 5))
    sns.barplot(data=q5, x="location_pair", y="trip_count", ax=ax, color="steelblue")
    ax.set_title("Top Pickup-Dropoff Pairs by Volume")
    ax.set_xlabel("Pickup→Dropoff Pair")
    ax.set_ylabel("Trip Count")
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    fig.savefig(OUTPUT_DIR / "chart_bar_hour_revenue.png", dpi=150)
    plt.close(fig)

    # 3) Scatter plot
    q2 = results["q2_distance_fare_correlation"].copy()
    fig, ax = plt.subplots(figsize=(8, 5))
    sns.scatterplot(
        data=q2,
        x="distance_bin",
        y="avg_fare",
        size="trip_count",
        hue="trip_count",
        ax=ax,
        sizes=(50, 600),
    )
    corr_val = float(q2["distance_fare_corr"].iloc[0]) if not q2.empty else 0.0
    ax.set_title(f"Distance vs Fare (corr={corr_val:.2f})")
    ax.set_xlabel("Trip Distance (miles, binned)")
    ax.set_ylabel("Average Fare")
    plt.tight_layout()
    fig.savefig(OUTPUT_DIR / "chart_scatter_revenue_duration.png", dpi=150)
    plt.close(fig)

    # 4) Summary dashboard with 3 subplots
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))

    sns.lineplot(data=q1_top, x="pickup_hour", y="trip_count", hue="pickup_weekday", marker="o", ax=axes[0, 0])
    axes[0, 0].set_title("Trip Volume by Hour")

    sns.barplot(data=q5, x="location_pair", y="trip_count", ax=axes[0, 1], color="darkcyan")
    axes[0, 1].set_title("Top Location Pairs")
    axes[0, 1].tick_params(axis="x", rotation=45)

    q3 = (
        results["q3_payment_preference_time_location"]
        .sort_values("trip_count", ascending=False)
        .head(12)
        .copy()
    )
    q3["slot"] = "L" + q3["PULocationID"].astype(str) + "-H" + q3["pickup_hour"].astype(str)
    sns.barplot(data=q3, x="slot", y="payment_share_pct", hue="payment_desc", ax=axes[1, 0])
    axes[1, 0].set_title("Payment Preference by Time+Location")
    axes[1, 0].tick_params(axis="x", rotation=45)

    q4 = results["q4_avg_tip_by_segments"].copy().head(12)
    sns.barplot(
        data=q4,
        x="payment_desc",
        y="avg_tip",
        hue="distance_bucket",
        ax=axes[1, 1],
        palette="magma",
    )
    axes[1, 1].set_title("Average Tip by Payment & Distance")
    axes[1, 1].tick_params(axis="x", rotation=30)

    for axis in axes.ravel():
        axis.set_xlabel(axis.get_xlabel())
        axis.set_ylabel(axis.get_ylabel())

    fig.suptitle("NYC Taxi Warehouse Dashboard", fontsize=16, fontweight="bold")
    plt.tight_layout()
    fig.savefig(OUTPUT_DIR / "chart_dashboard.png", dpi=150)
    plt.close(fig)


def main():
    if not PYSPARK_AVAILABLE:
        raise RuntimeError(
            "PySpark is required for Assignment 03 analytics on HDFS warehouse tables."
        )
    spark = spark_session()
    try:
        query_results = run_queries(spark)
    finally:
        spark.stop()
    generate_visuals(query_results)


if __name__ == "__main__":
    main()
