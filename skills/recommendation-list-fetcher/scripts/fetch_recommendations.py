#!/usr/bin/env python3
"""Fetch and parse stock recommendations from a Google Sheet.

CLI entry point for the recommendation-list-fetcher skill.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from shopping_list import (
    fetch_recommendations,
    validate_config,
)


def main():
    parser = argparse.ArgumentParser(
        description="Fetch stock recommendations from Google Sheet"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/recommendation_list_config.yaml"),
        help="Path to config file (default: config/recommendation_list_config.yaml)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output JSON path (default: reports/daily/recommendations_YYYY-MM-DD.json)",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore cache and fetch fresh data",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse but don't write output files",
    )
    parser.add_argument(
        "--validate-config",
        action="store_true",
        help="Validate config file and exit",
    )
    parser.add_argument(
        "--validate-access",
        action="store_true",
        help="Test sheet access and exit",
    )
    parser.add_argument(
        "--url",
        help="Override source URL for access validation",
    )

    args = parser.parse_args()

    # Validate config
    if not args.config.exists():
        print(f"ERROR: Config file not found: {args.config}", file=sys.stderr)
        print(
            f"Create {args.config} using assets/config_template.yaml as a template.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        config = validate_config(args.config)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if args.validate_config:
        print(f"✓ Config valid")
        print(f"  Source URL: {config['source']['url']}")
        print(f"  Sheet ID: {config['source']['sheet_id']}")
        print(f"  Data starts at row: {config['source']['data_starts_at_row']}")
        sys.exit(0)

    if args.validate_access:
        url = args.url or config["source"]["url"]
        from shopping_list import test_sheet_access
        success, msg = test_sheet_access(url)
        print(msg)
        sys.exit(0 if success else 1)

    # Fetch recommendations
    try:
        result = fetch_recommendations(
            config=config,
            force_refresh=args.force_refresh,
        )
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        today = datetime.now().strftime("%Y-%m-%d")
        output_path = Path(f"reports/daily/recommendations_{today}.json")

    # Write output
    if not args.dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"✓ Wrote {output_path}")
    else:
        print(f"(dry-run) Would write {output_path}")

    # Summary
    source = result["source"]
    summary = result["summary"]
    print(f"\nSummary:")
    print(f"  Parsed: {source['row_count_parsed']}/{source['row_count_total']}")
    print(f"  BUY: {summary['buy_count']}, SELL: {summary['sell_count']}, "
          f"HOLD: {summary['hold_count']}")
    if source["cached"]:
        print(f"  Cached: {source.get('cache_age_minutes', '?')} min old")
    if source["stale"]:
        print(f"  WARNING: Cache is stale")

    return 0


if __name__ == "__main__":
    sys.exit(main())
