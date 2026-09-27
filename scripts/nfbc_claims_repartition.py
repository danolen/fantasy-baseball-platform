#!/usr/bin/env python3
"""Move already-uploaded NFBC claims CSVs into today's date partition (#298).

Objects currently at::

    s3://dn-lakehouse-dev/nfbc/claims/{online_championship|main_event}/claims_{id}.csv

are copied to::

    s3://dn-lakehouse-dev/nfbc/claims/{format}/year=YYYY/month=MM/day=DD/claims_{id}.csv

then the unpartitioned source is deleted. ``year=/month=/day=`` uses
``America/New_York`` (same as other NFBC ingest prefixes).

Only regular ``claims_{id}.csv`` keys sitting directly in a format folder
are moved. Already-partitioned keys, other filenames, and other prefixes
are never copied or deleted.

Preview first::

    python scripts/nfbc_claims_repartition.py --dry-run

Then apply::

    python scripts/nfbc_claims_repartition.py
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from nfbc_claims import (  # noqa: E402
    ClaimsUploadError,
    DEFAULT_S3_BASE,
    date_partition_path,
    is_unpartitioned_claims_key,
    parse_s3_uri,
    partitioned_claims_dest_key,
)


def _s3_client(s3_client=None):
    if s3_client is not None:
        return s3_client
    import boto3

    return boto3.client("s3")


def list_unpartitioned_claims_keys(client, bucket: str, prefix: str) -> list[str]:
    """List ``{prefix}/{format}/claims_{id}.csv`` objects with no date folder."""
    keys: list[str] = []
    token = None
    list_prefix = f"{prefix}/" if prefix else ""
    while True:
        kwargs: dict = {"Bucket": bucket, "Prefix": list_prefix}
        if token:
            kwargs["ContinuationToken"] = token
        response = client.list_objects_v2(**kwargs)
        for obj in response.get("Contents") or []:
            key = obj.get("Key") or ""
            if is_unpartitioned_claims_key(key, prefix):
                keys.append(key)
        if not response.get("IsTruncated"):
            break
        token = response.get("NextContinuationToken")
        if not token:
            break
    return keys


def _missing_object_error(exc: Exception) -> bool:
    name = type(exc).__name__
    if name in {"NoSuchKey", "404", "KeyError"}:
        return True
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NotFound"}:
            return True
    return "Not Found" in str(exc)


def _object_exists(client, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if _missing_object_error(exc):
            return False
        raise
    return True


def delete_unpartitioned_claims_object(
    client, bucket: str, key: str, base_prefix: str
) -> None:
    """Delete only an unpartitioned ``{format}/claims_{id}.csv`` source key."""
    if not is_unpartitioned_claims_key(key, base_prefix):
        raise ClaimsUploadError(
            f"Refusing to delete s3://{bucket}/{key}; only an unpartitioned "
            f"claims_{{id}}.csv in a format folder may be removed"
        )
    client.delete_object(Bucket=bucket, Key=key)


def move_unpartitioned_claims(
    *,
    s3_base: str = DEFAULT_S3_BASE,
    when: date | None = None,
    dry_run: bool = False,
    s3_client=None,
) -> int:
    """Copy unpartitioned claims CSVs into today's partition, then delete sources."""
    bucket, prefix = parse_s3_uri(s3_base)
    client = _s3_client(s3_client)
    sources = list_unpartitioned_claims_keys(client, bucket, prefix)
    partition = date_partition_path(when)
    if not sources:
        print(f"No unpartitioned claims CSVs under s3://{bucket}/{prefix}/")
        return 0

    print(
        f"{'Dry-run: would move' if dry_run else 'Moving'} {len(sources)} "
        f"claims CSV(s) into {partition}"
    )
    failures: list[str] = []
    moved = 0
    skipped = 0
    for source in sources:
        dest = partitioned_claims_dest_key(source, when=when)
        print(f"  s3://{bucket}/{source} -> s3://{bucket}/{dest}")
        if dry_run:
            moved += 1
            continue
        try:
            if _object_exists(client, bucket, dest):
                print("    skip: destination already exists", file=sys.stderr)
                skipped += 1
                continue
            client.copy_object(
                Bucket=bucket,
                Key=dest,
                CopySource={"Bucket": bucket, "Key": source},
            )
            if not _object_exists(client, bucket, dest):
                raise ClaimsUploadError(
                    f"Copy reported success but s3://{bucket}/{dest} is missing"
                )
            delete_unpartitioned_claims_object(client, bucket, source, prefix)
            moved += 1
        except Exception as exc:
            failures.append(f"{source}: {exc}")
            print(f"    failed: {exc}", file=sys.stderr)

    print(f"Finished: moved {moved}, skipped {skipped}, failed {len(failures)}.")
    if failures:
        for item in failures:
            print(f"  {item}", file=sys.stderr)
        return 1
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Move unpartitioned NFBC claims CSVs into today's "
            "year=/month=/day= folder under the same format prefix."
        )
    )
    parser.add_argument(
        "--s3-base",
        default=DEFAULT_S3_BASE,
        help=f"Claims prefix (default: {DEFAULT_S3_BASE}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List planned moves without copying or deleting anything.",
    )
    parser.add_argument(
        "--date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Partition date (default: today in America/New_York).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    when = date.fromisoformat(args.date) if args.date else None
    try:
        return move_unpartitioned_claims(
            s3_base=args.s3_base,
            when=when,
            dry_run=args.dry_run,
        )
    except (ClaimsUploadError, ValueError) as exc:
        print(f"Failed to repartition claims: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
