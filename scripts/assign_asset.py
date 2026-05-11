"""Assign a GoCodes asset to a person.

You can pass an explicit asset id, or pass --find "<name or serial>" to let the
script look the asset up first. The script writes the assignment back to
GoCodes; re-run sync_gocodes_to_glean.py afterwards (or schedule it) so Glean
reflects the new state.

Examples:
  python scripts/assign_asset.py --id 1234 --to alice@example.com \
      --name "Alice Wong" --location "HQ - Floor 2" \
      --note "Issued for onsite Q2 install"

  python scripts/assign_asset.py --find "Dewalt drill #3" \
      --to bob@example.com --location "Van 7"
"""
import argparse
import sys

from gocodes_glean import config
from gocodes_glean.gocodes import GoCodesClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Assign a GoCodes asset.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--id", help="GoCodes asset id")
    group.add_argument("--find", help="Name or serial to look up the asset by")

    parser.add_argument("--to", required=True, help="Assignee email")
    parser.add_argument("--name", default="", help="Assignee display name")
    parser.add_argument("--location", default="", help="New location (optional)")
    parser.add_argument("--note", default="", help="Assignment note (optional)")
    args = parser.parse_args()

    cfg = config.load()
    gc = GoCodesClient(cfg)

    if args.id:
        asset_id = args.id
    else:
        match = gc.find_asset(args.find)
        if not match:
            print(f"No GoCodes asset matched: {args.find!r}", file=sys.stderr)
            sys.exit(2)
        asset_id = match.id
        print(f"Matched asset {asset_id}: {match.name}")

    updated = gc.assign_asset(
        asset_id=asset_id,
        assignee_email=args.to,
        assignee_name=args.name,
        location=args.location,
        note=args.note,
    )
    print(
        f"Assigned asset {updated.id} ({updated.name}) -> "
        f"{updated.assigned_to_name or updated.assigned_to_email} "
        f"@ {updated.location or 'no location set'}"
    )


if __name__ == "__main__":
    main()
