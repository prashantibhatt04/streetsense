"""
One-time TTC GTFS cache builder.

Downloads TTC GTFS static data from Toronto Open Data and writes three
cache files to gtfs_cache/ (routes.json, stops.json, stop_routes.json).
Takes ~30 seconds on first run. Re-run with --force to refresh.

Usage:
    python3 -m scripts.download_gtfs
    python3 -m scripts.download_gtfs --force
"""
import argparse
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build TTC GTFS cache")
    parser.add_argument("--force", action="store_true", help="Re-download even if cache exists")
    args = parser.parse_args()

    from tools.gtfs_tools import build_gtfs_cache, GTFS_CACHE_DIR
    print(f"Cache directory: {GTFS_CACHE_DIR}")

    if not args.force and (GTFS_CACHE_DIR / "stops.json").exists():
        print("Cache already exists. Use --force to refresh.")
        return

    print("Downloading TTC GTFS (this takes ~30 seconds) …")
    success = build_gtfs_cache(force=args.force)
    if success:
        print("Done. GTFS cache is ready.")
    else:
        print("Failed — check logs above.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
