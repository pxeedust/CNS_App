"""Filter generated email JSON using a separate failed-recipient file.

Usage:
    python filtered.py generated_emails.json failed_recipients.txt --output retry_emails.json

The failed-recipient file must contain one email address per line. This helper
never overwrites the source file and contains no real contact data.
"""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("failed", type=Path)
    parser.add_argument("--output", type=Path, default=Path("retry_emails.json"))
    args = parser.parse_args()

    failed = {
        line.strip().lower()
        for line in args.failed.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    records = json.loads(args.source.read_text(encoding="utf-8"))
    filtered = [
        record
        for record in records
        if str(record.get("email_address", "")).strip().lower() in failed
    ]
    args.output.write_text(json.dumps(filtered, indent=2), encoding="utf-8")
    print(f"Wrote {len(filtered)} records to {args.output}")


if __name__ == "__main__":
    main()
