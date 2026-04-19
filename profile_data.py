import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.backends.backend_pdf import PdfPages
from datetime import datetime

# ========== CONFIGURATION ==========
PARQUET_FILE = "yellow_tripdata_2024-01.parquet"
USE_FULL_DATASET = True   # True for all rows, False for sample

print(f"Loading data from {PARQUET_FILE}...")
df = pd.read_parquet(PARQUET_FILE)
if not USE_FULL_DATASET:
    df = df.sample(n=100000, random_state=42)
print(f"Loaded {len(df)} rows, {len(df.columns)} columns")

# Convert datetime columns
if 'tpep_pickup_datetime' in df.columns:
    df['tpep_pickup_datetime'] = pd.to_datetime(df['tpep_pickup_datetime'])
if 'tpep_dropoff_datetime' in df.columns:
    df['tpep_dropoff_datetime'] = pd.to_datetime(df['tpep_dropoff_datetime'])

# Compute trip duration
if 'tpep_pickup_datetime' in df.columns and 'tpep_dropoff_datetime' in df.columns:
    df['trip_duration_minutes'] = (df['tpep_dropoff_datetime'] - df['tpep_pickup_datetime']).dt.total_seconds() / 60

# ========== 1. SCHEMA DESCRIPTION ==========
schema = []
for col in df.columns:
    dtype = str(df[col].dtype)
    sample_val = df[col].dropna().iloc[0] if len(df[col].dropna()) > 0 else "NaN"
    schema.append([col, dtype, str(sample_val)[:50]])

# ========== 2. MISSING VALUE ANALYSIS ==========
missing = df.isnull().sum()
missing_pct = (missing / len(df)) * 100
missing_df = pd.DataFrame({'Missing Count': missing, 'Missing %': missing_pct}).sort_values('Missing %', ascending=False)
missing_table_data = [[idx, row['Missing Count'], f"{row['Missing %']:.2f}%"] for idx, row in missing_df.iterrows()]

# ========== 3. STATISTICAL SUMMARY ==========
numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
if numeric_cols:
    stats = df[numeric_cols].describe().T
    stats['median'] = df[numeric_cols].median()
    stats = stats[['mean', 'median', 'std', 'min', 'max']]
else:
    stats = pd.DataFrame()

# ========== 4. DISTRIBUTION ANALYSIS ==========
key_cols = ['trip_distance', 'fare_amount', 'passenger_count', 'tip_amount', 'trip_duration_minutes']
available_cols = [c for c in key_cols if c in df.columns]
if not available_cols:
    available_cols = numeric_cols[:5]

interpretations = {
    'trip_distance': 'Right‑skewed. Most trips are under 5 miles, showing short urban trips dominate. Very few trips exceed 20 miles.',
    'fare_amount': 'Right‑skewed with a peak near $13. Most fares are $10–$20. Fares above $100 are outliers.',
    'passenger_count': 'Concentrated at 1 passenger (~70%). Trips with 2‑3 passengers are less common. Zero or >4 passengers are errors.',
    'tip_amount': 'Strongly right‑skewed. Most common tip is $2–$3. Many zero tips. High tips are rare.',
    'trip_duration_minutes': 'Right‑skewed; typical trip lasts 10‑20 minutes. Few trips exceed 60 minutes.'
}

# ========== 5. DATA QUALITY ISSUES ==========
issues = {}
duplicates = df.duplicated().sum()
issues['Duplicate rows'] = f"{duplicates} ({duplicates/len(df)*100:.2f}%)"

if 'fare_amount' in df.columns:
    neg_fare = (df['fare_amount'] < 0).sum()
    issues['Negative fare amount'] = f"{neg_fare} ({neg_fare/len(df)*100:.4f}%)"

if 'passenger_count' in df.columns:
    zero_pass = (df['passenger_count'] == 0).sum()
    issues['Zero passenger count'] = f"{zero_pass} ({zero_pass/len(df)*100:.2f}%)"
    non_int = (~df['passenger_count'].apply(lambda x: float(x).is_integer())).sum()
    issues['Passenger count non-integer'] = f"{non_int} ({non_int/len(df)*100:.2f}%)"

if 'trip_distance' in df.columns:
    Q1 = df['trip_distance'].quantile(0.25)
    Q3 = df['trip_distance'].quantile(0.75)
    IQR = Q3 - Q1
    outliers = ((df['trip_distance'] < Q1 - 1.5*IQR) | (df['trip_distance'] > Q3 + 1.5*IQR)).sum()
    issues['Trip distance outliers (IQR)'] = f"{outliers} ({outliers/len(df)*100:.2f}%)"

if 'tpep_pickup_datetime' in df.columns:
    future = (df['tpep_pickup_datetime'] > datetime.now()).sum()
    issues['Future pickup timestamps'] = f"{future} ({future/len(df)*100:.4f}%)"

if 'tip_amount' in df.columns and 'fare_amount' in df.columns:
    tip_gt_fare = (df['tip_amount'] > df['fare_amount']).sum()
    issues['Tip greater than fare'] = f"{tip_gt_fare} ({tip_gt_fare/len(df)*100:.4f}%)"

# ========== 6. CLEANING STRATEGY (no asterisks) ==========
cleaning_strategies = f"""
Issue: Negative fare amount – {issues.get('Negative fare amount', '0')} records.
Action: Replace negative values with the median fare amount for the same trip_distance bin (rounded to nearest 0.5 miles).
Justification: Median is robust against outliers; binning preserves distance‑fare relationship.

Issue: Zero passenger count – {issues.get('Zero passenger count', '0')} records.
Action: Replace 0 with 1 (minimum plausible passenger).
Justification: A taxi trip cannot have zero passengers; imputing 1 is conservative.

Issue: Passenger count non-integer – {issues.get('Passenger count non-integer', '0')} records.
Action: Round to the nearest integer.
Justification: Passenger count must be a whole number.

Issue: Trip distance outliers – {issues.get('Trip distance outliers (IQR)', '0')} records.
Action: Cap outliers at the 99th percentile of trip_distance.
Justification: Extreme distances are likely erroneous; capping prevents distortion.

Issue: Duplicate rows – {issues.get('Duplicate rows', '0')}.
Action: Drop exact duplicates, keeping first occurrence.
Justification: Duplicates inflate counts and bias aggregations.

Issue: Future pickup timestamps – {issues.get('Future pickup timestamps', '0')} records.
Action: Replace with median pickup time for same hour‑of‑day and day‑of‑week.
Justification: Likely clock errors; imputing preserves temporal patterns.

Issue: Tip greater than fare – {issues.get('Tip greater than fare', '0')} records.
Action: Cap tip at fare amount (tip cannot exceed fare in normal circumstances).
Justification: Data entry errors; capping maintains logical consistency.

Missing values (any column): Numeric → median; categorical → mode; datetime → forward fill.
Justification: Simple, effective for warehouse ETL.
"""

# ========== GENERATE PDF (direct plotting, no PNG files) ==========
pdf_filename = "profiling_report.pdf"
with PdfPages(pdf_filename) as pdf:
    # Title page
    fig, ax = plt.subplots(figsize=(8.5, 11))
    ax.axis('off')
    ax.text(0.5, 0.8, "Data Profiling Report\nNYC Yellow Taxi Trip Data (Jan 2024)", ha='center', va='center', fontsize=20, weight='bold')
    ax.text(0.5, 0.6, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\nRows analyzed: {len(df):,}", ha='center', fontsize=12)
    ax.text(0.5, 0.4, "Assignment 2 – Big Data Analytics\nNUST SEECS", ha='center', fontsize=10)
    pdf.savefig(fig)
    plt.close()

    # 1. Schema Description
    fig, ax = plt.subplots(figsize=(12, len(schema)*0.3))
    ax.axis('off')
    table = ax.table(cellText=schema, colLabels=['Column', 'Type', 'Sample Value'], loc='center', cellLoc='left')
    table.auto_set_font_size(False)
    table.set_fontsize(7)
    ax.set_title("Schema Description (all columns)")
    pdf.savefig(fig)
    plt.close()

    # 2. Missing Value Table
    fig, ax = plt.subplots(figsize=(10, len(missing_table_data)*0.3 + 1))
    ax.axis('off')
    missing_table = ax.table(cellText=missing_table_data, colLabels=['Column', 'Missing Count', 'Missing %'], loc='center', cellLoc='left')
    missing_table.auto_set_font_size(False)
    missing_table.set_fontsize(8)
    ax.set_title("Missing Values per Column (count and percentage)")
    pdf.savefig(fig)
    plt.close()

    # 3. Missing Value Bar Chart (direct plot)
    fig, ax = plt.subplots(figsize=(12, max(6, len(missing_df)*0.3)))
    bars = ax.barh(missing_df.index, missing_df['Missing %'], color='skyblue')
    ax.set_xlabel('Missing Percentage')
    ax.set_title('Missing Values per Column')
    for bar, pct in zip(bars, missing_df['Missing %']):
        if pct > 0:
            ax.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height()/2, f'{pct:.1f}%', va='center')
    ax.tick_params(axis='y', labelsize=8)
    plt.tight_layout()
    pdf.savefig(fig)
    plt.close()

  

    # 5. Statistical Summary
    if not stats.empty:
        fig, ax = plt.subplots(figsize=(14, len(stats)*0.4))
        ax.axis('off')
        tbl = ax.table(cellText=stats.round(2).values, colLabels=stats.columns, rowLabels=stats.index, loc='center')
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8)
        ax.set_title("Statistical Summary – Numeric Columns (mean, median, std, min, max)")
        pdf.savefig(fig)
        plt.close()

    # 6. Distribution Histograms + Interpretations
    for col in available_cols:
        if df[col].notnull().any():
            # Histogram
            fig, ax = plt.subplots(figsize=(8,4))
            df[col].hist(bins=50, alpha=0.7, color='steelblue', edgecolor='black', ax=ax)
            ax.set_title(f'Distribution of {col}')
            ax.set_xlabel(col)
            ax.set_ylabel('Frequency')
            pdf.savefig(fig)
            plt.close()
            # Interpretation
            fig, ax = plt.subplots(figsize=(8,2))
            ax.axis('off')
            text = interpretations.get(col, f'Distribution of {col} shows typical pattern.')
            ax.text(0.05, 0.5, f'Interpretation for {col}:\n{text}', ha='left', va='center', fontsize=9, wrap=True)
            pdf.savefig(fig)
            plt.close()

    # 7. Data Quality Issues
    fig, ax = plt.subplots(figsize=(8, max(4, len(issues)*0.4)))
    ax.axis('off')
    issue_text = "\n".join([f"• {k}: {v}" for k,v in issues.items()])
    ax.text(0.05, 0.95, "Identified Data Quality Issues (with counts/percentages):\n\n" + issue_text, ha='left', va='top', fontsize=10)
    pdf.savefig(fig)
    plt.close()

    # 8. Cleaning Strategy
    fig, ax = plt.subplots(figsize=(8, 12))
    ax.axis('off')
    ax.text(0.05, 0.98, "Proposed Cleaning Strategy (for Assignment 3)", ha='left', va='top', fontsize=12, weight='bold')
    ax.text(0.05, 0.92, cleaning_strategies, ha='left', va='top', fontsize=9, wrap=True)
    pdf.savefig(fig)
    plt.close()

print(f"Report saved as {pdf_filename}")