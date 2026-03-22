from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Tuple

import boto3


def _parse_s3_uri(s3_base_path: str) -> Tuple[str, str]:
    """Return (bucket, key_prefix_without_leading_or_trailing_slash)."""
    if not s3_base_path.startswith("s3://"):
        raise ValueError("S3 path must start with s3://")

    s3_path = s3_base_path[5:]
    parts = [p for p in s3_path.split("/") if p]
    if not parts:
        raise ValueError("S3 path must include a bucket name (e.g. s3://my-bucket/prefix)")

    bucket = parts[0]
    base_prefix = "/".join(parts[1:])
    return bucket, base_prefix


def _partition_path() -> str:
    today = date.today()
    return (
        f"year={today.year}/"
        f"month={today.month:02d}/"
        f"day={today.day:02d}"
    )


def _full_s3_prefix(base_prefix: str) -> str:
    partition = _partition_path()
    if not base_prefix:
        return partition
    return f"{base_prefix}/{partition}"


def upload_to_s3(local_path: str, s3_base_path: str) -> None:
    """
    Upload file(s) to S3 under a date partition (year=/month=/day=) below the
    given base prefix. If local_path is a directory, uploads only regular files
    in that directory (not subdirectories).
    """
    path = Path(local_path).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"Local path does not exist: {local_path}")

    bucket, base_prefix = _parse_s3_uri(s3_base_path)
    full_prefix = _full_s3_prefix(base_prefix)

    s3 = boto3.client("s3")

    if path.is_file():
        s3_key = f"{full_prefix}/{path.name}"
        print(f"Uploading {path} -> s3://{bucket}/{s3_key}")
        s3.upload_file(str(path), bucket, s3_key)
        print("Upload complete.")
        return

    if path.is_dir():
        uploaded = 0
        for child in sorted(path.iterdir()):
            if not child.is_file():
                continue
            s3_key = f"{full_prefix}/{child.name}"
            print(f"Uploading {child} -> s3://{bucket}/{s3_key}")
            s3.upload_file(str(child), bucket, s3_key)
            uploaded += 1
        if uploaded == 0:
            print("No files uploaded (folder is empty or contains no regular files).")
        else:
            print("Upload complete.")
        return

    raise ValueError(f"Local path is not a file or directory: {local_path}")


def upload_folder_to_s3(local_path: str, s3_base_path: str) -> None:
    """Backward-compatible name; see upload_to_s3."""
    upload_to_s3(local_path, s3_base_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Upload a file or all files from a local folder to S3 with "
            "date partitions (year=/month=/day=)."
        )
    )
    parser.add_argument(
        "--local-path",
        required=True,
        help="Local file or folder to upload (use quotes if the path contains spaces)",
    )
    parser.add_argument(
        "--s3-path",
        required=True,
        help="Base S3 URI (e.g. s3://my-bucket/folder/prefix)",
    )

    args = parser.parse_args()

    upload_to_s3(local_path=args.local_path, s3_base_path=args.s3_path)
