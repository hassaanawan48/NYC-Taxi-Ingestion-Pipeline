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
        "q1_monthly_revenue_trend": """
            -- Business Q1: How does total revenue trend over time?
            SELECT
                CONCAT(CAST(pickup_year AS STRING), '-', LPAD(CAST(pickup_month AS STRING), 2, '0')) AS year_month,
                SUM(total_revenue) AS total_revenue,
                COUNT(*) AS total_trips
            FROM fact_trips
            GROUP BY pickup_year, pickup_month
            ORDER BY pickup_year, pickup_month
        """,
        "q2_top_hours_ranked": """
            -- Business Q2: Which pickup hours generate highest revenue?
            SELECT
                d.pickup_hour,
                SUM(f.total_revenue) AS hour_revenue,
                RANK() OVER (ORDER BY SUM(f.total_revenue) DESC) AS revenue_rank
            FROM fact_trips f
            JOIN dim_datetime d ON f.datetime_key = d.datetime_key
            GROUP BY d.pickup_hour
            ORDER BY revenue_rank
        """,
        "q3_payment_growth_lag": """
            -- Business Q3: How does payment-type trip volume change month-over-month?
            SELECT
                p.payment_desc,
                f.pickup_year,
                f.pickup_month,
                COUNT(*) AS trips,
                LAG(COUNT(*)) OVER (
                    PARTITION BY p.payment_desc
                    ORDER BY f.pickup_year, f.pickup_month
                ) AS prev_month_trips
            FROM fact_trips f
            JOIN dim_payment p ON f.payment_type = p.payment_type
            GROUP BY p.payment_desc, f.pickup_year, f.pickup_month
            ORDER BY p.payment_desc, f.pickup_year, f.pickup_month
        """,
        "q4_distance_bucket_kpis": """
            -- Business Q4: Which trip distance buckets are most profitable?
            SELECT
                distance_bucket,
                COUNT(*) AS trip_count,
                ROUND(AVG(total_revenue), 2) AS avg_revenue_per_trip,
                ROUND(AVG(trip_duration_minutes), 2) AS avg_duration_min
            FROM fact_trips
            GROUP BY distance_bucket
            ORDER BY avg_revenue_per_trip DESC
        """,
        "q5_vendor_productivity_rownum": """
            -- Business Q5: Top vendor-hour combinations by revenue productivity.
            WITH vendor_hour AS (
                SELECT
                    f.VendorID,
                    d.pickup_hour,
                    SUM(f.total_revenue) AS revenue,
                    COUNT(*) AS trips
                FROM fact_trips f
                JOIN dim_datetime d ON f.datetime_key = d.datetime_key
                GROUP BY f.VendorID, d.pickup_hour
            )
            SELECT
                VendorID,
                pickup_hour,
                revenue,
                trips,
                ROW_NUMBER() OVER (PARTITION BY VendorID ORDER BY revenue DESC) AS rn
            FROM vendor_hour
            ORDER BY VendorID, rn
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
    q1 = results["q1_monthly_revenue_trend"].copy()
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(q1["year_month"], q1["total_revenue"], marker="o")
    ax.set_title("Monthly Revenue Trend")
    ax.set_xlabel("Month")
    ax.set_ylabel("Total Revenue")
    plt.xticks(rotation=45)
    plt.tight_layout()
    fig.savefig(OUTPUT_DIR / "chart_line_trend.png", dpi=150)
    plt.close(fig)

    # 2) Bar chart
    q2 = results["q2_top_hours_ranked"].copy().sort_values("revenue_rank").head(12)
    fig, ax = plt.subplots(figsize=(10, 5))
    sns.barplot(data=q2, x="pickup_hour", y="hour_revenue", ax=ax, color="steelblue")
    ax.set_title("Revenue by Pickup Hour")
    ax.set_xlabel("Hour of Day")
    ax.set_ylabel("Revenue")
    plt.tight_layout()
    fig.savefig(OUTPUT_DIR / "chart_bar_hour_revenue.png", dpi=150)
    plt.close(fig)

    # 3) Scatter plot
    q4 = results["q4_distance_bucket_kpis"].copy()
    fig, ax = plt.subplots(figsize=(8, 5))
    sns.scatterplot(
        data=q4,
        x="avg_duration_min",
        y="avg_revenue_per_trip",
        size="trip_count",
        hue="distance_bucket",
        ax=ax,
        sizes=(100, 900),
    )
    ax.set_title("Revenue vs Duration by Distance Bucket")
    ax.set_xlabel("Average Duration (minutes)")
    ax.set_ylabel("Average Revenue per Trip")
    plt.tight_layout()
    fig.savefig(OUTPUT_DIR / "chart_scatter_revenue_duration.png", dpi=150)
    plt.close(fig)

    # 4) Summary dashboard with 3 subplots
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))

    axes[0, 0].plot(q1["year_month"], q1["total_revenue"], marker="o")
    axes[0, 0].set_title("Revenue Trend")
    axes[0, 0].tick_params(axis="x", rotation=45)

    sns.barplot(data=q2, x="pickup_hour", y="hour_revenue", ax=axes[0, 1], color="darkcyan")
    axes[0, 1].set_title("Top Hours by Revenue")

    sns.barplot(
        data=q4,
        x="distance_bucket",
        y="avg_revenue_per_trip",
        hue="distance_bucket",
        legend=False,
        ax=axes[1, 0],
        palette="viridis",
    )
    axes[1, 0].set_title("Avg Revenue by Distance Bucket")

    sns.barplot(
        data=q4,
        x="distance_bucket",
        y="avg_duration_min",
        hue="distance_bucket",
        legend=False,
        ax=axes[1, 1],
        palette="magma",
    )
    axes[1, 1].set_title("Avg Duration by Distance Bucket")

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
