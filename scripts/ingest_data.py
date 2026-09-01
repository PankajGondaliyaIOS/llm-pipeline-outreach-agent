"""
scripts/ingest_data.py - High-Performance Streaming Batch Ingestion.
Streams 300k+ rows in chunks of 10,000 to prevent RAM exhaustion and maximizes SQLite write IOPS.
"""

import asyncio
import os
import re
import time
from typing import Optional
import pandas as pd
from src.database import DatabaseManager


def extract_app_id(url: str, default_name: str) -> str:
    """Extracts package id from Play Store URL or generates a fallback slug."""
    match = re.search(r"id=([a-zA-Z0-9._]+)", str(url))
    if match:
        return match.group(1)
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "", default_name).lower()
    return cleaned if cleaned else f"app_{int(time.time() * 1000)}"


async def import_leads(file_path: str, chunk_size: int = 10000) -> None:
    db = DatabaseManager()
    await db.init_db()

    if not os.path.exists(file_path):
        print(f"[✗] File not found: {file_path}")
        return

    print(f"[*] Reading high-volume dataset: {file_path} (Streaming chunk size: {chunk_size:,})...")
    start_time = time.time()
    total_ingested = 0

    # Stream CSV in chunks instead of loading all 300,000 rows into RAM at once
    chunks = pd.read_csv(
        file_path,
        chunksize=chunk_size,
        dtype=str,  # Read everything as str to prevent type inference bottlenecks
        low_memory=False,
    )

    for chunk_idx, df in enumerate(chunks, 1):
        df.columns = df.columns.str.strip()
        batch_records = []

        for _, row in df.iterrows():
            app_name = str(row.get("App Name") or "").strip()
            if not app_name or app_name == "nan":
                continue

            category = str(row.get("Category") or "Tools").strip()
            raw_installs = str(row.get("Minimum Installs") or "0").split(".")[0].strip()
            min_installs = int(raw_installs) if raw_installs.isdigit() else 0

            developer_id = str(row.get("Developer Id") or "").strip()
            dev_email = row.get("Developer Email")
            developer_email = str(dev_email).strip().lower() if pd.notna(dev_email) and str(dev_email).strip() != "nan" else None

            dev_website = row.get("Developer Website")
            developer_website = str(dev_website).strip() if pd.notna(dev_website) and str(dev_website).strip() != "nan" else None

            playstore_url = str(row.get("PlayStore_URL") or "").strip()
            app_id = extract_app_id(playstore_url, app_name)

            batch_records.append({
                "app_id": app_id,
                "app_name": app_name,
                "category": category,
                "min_installs": min_installs,
                "developer_id": developer_id,
                "developer_email": developer_email,
                "developer_website": developer_website,
                "playstore_url": playstore_url,
            })

        # Flush chunk directly to SQLite via single transaction
        if batch_records:
            count = await db.upsert_leads_bulk(batch_records)
            total_ingested += count
            print(f"[>] Processed chunk {chunk_idx}: {total_ingested:,} rows loaded...")

    elapsed = time.time() - start_time
    print(f"[✓] Completed ingestion of {total_ingested:,} leads in {elapsed:.1f}s ({(total_ingested / max(elapsed, 0.1)):,.0f} rows/sec).")


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "leads.csv"
    asyncio.run(import_leads(target))