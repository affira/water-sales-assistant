"""Build the structured service-record index (calls Claude once per changed report).

    python -m aquaassist.ingest           extract new/changed reports only
    python -m aquaassist.ingest --force   re-extract everything
"""
from __future__ import annotations

import argparse

from . import config
from .extraction import extract_all


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    records = extract_all(force=args.force)
    flagged = [r for r in records if r["checks"]]
    print(f"\n{len(records)} records in {config.RECORDS_PATH.relative_to(config.ROOT)}; "
          f"{len(flagged)} with validation flags.")


if __name__ == "__main__":
    main()
