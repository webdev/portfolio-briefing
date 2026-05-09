#!/usr/bin/env python3
"""Test live sheet access and parsing."""

import csv
import io
from datetime import date

# Test live sheet access
print("Testing live sheet access...")

try:
    import httpx
except ImportError:
    print("✗ httpx not installed. Install with: pip install httpx")
    exit(1)

# The public gviz CSV endpoint for the recommendation sheet
url = "https://docs.google.com/spreadsheets/d/12Fs_d8Zr4sKnoCxb5EaEbe2FciXIGPVTFGM9iehZq3M/gviz/tq?tqx=out:csv"

try:
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(url)
        resp.raise_for_status()

    csv_text = resp.text
    print(f"✓ Sheet accessible ({len(csv_text)} bytes)")

    # Parse CSV
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)

    print(f"✓ CSV parsed ({len(rows)} rows total)")

    # Check header
    if rows and rows[0]:
        print(f"✓ Header row: {rows[0][:7]}")

    # Skip header and count data rows
    data_rows = rows[1:] if rows[0][0] == "Name" else rows

    # Count rating tiers
    rating_tiers = {
        "Top Stock to Buy": 0,
        "Top 15 Stock": 0,
        "Buy": 0,
        "Borderline Buy": 0,
        "Hold/ Market Perform": 0,
        "Sell": 0,
    }

    for row in data_rows:
        if len(row) < 2:
            continue
        name = row[0].strip()
        rating = row[1].strip()

        # Skip headers and footnotes
        if name == "Name" or name.startswith("*"):
            continue

        # Normalize rating for matching (handle trailing spaces)
        rating_norm = rating
        if rating == "Top 15 Stock ":
            rating_norm = "Top 15 Stock"

        if rating_norm in rating_tiers:
            rating_tiers[rating_norm] += 1

    print(f"\nRating Distribution:")
    for rating, count in sorted(
        rating_tiers.items(),
        key=lambda x: x[1],
        reverse=True,
    ):
        if count > 0:
            print(f"  {rating}: {count}")

    total_with_ratings = sum(rating_tiers.values())
    print(f"  Total with valid rating: {total_with_ratings}")

    # Verify sample data
    print(f"\nSample entries (first 3):")
    sample_count = 0
    for row in data_rows[:20]:
        if sample_count >= 3:
            break
        if len(row) >= 3:
            name = row[0].strip()
            rating = row[1].strip()
            date_str = row[2].strip()

            if name and rating and name != "Name" and not name.startswith("*"):
                print(f"  {name:30} | {rating:25} | {date_str:10}")
                sample_count += 1

    print("\n✓ Live sheet test passed!")

except httpx.HTTPError as e:
    print(f"✗ HTTP error: {e}")
    exit(1)
except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc()
    exit(1)
