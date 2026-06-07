from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import boto3

DEFAULT_DOWNLOAD_URL = "https://www.smartfantasybaseball.com/PLAYERIDMAPCSV"
DEFAULT_S3_URI = "s3://dn-lakehouse-dev/mapping/mpd_player_id_map/"
DEFAULT_OBJECT_NAME = "SFBB Player ID Map - PLAYERIDMAP.csv"
USER_AGENT = "fantasy-baseball-platform-ingest/1.0"


def _parse_s3_uri(s3_uri: str) -> Tuple[str, str]:
    if not s3_uri.startswith("s3://"):
        raise ValueError("S3 URI must start with s3://")

    path = s3_uri[5:]
    parts = [p for p in path.split("/") if p]
    if not parts:
        raise ValueError("S3 URI must include a bucket (e.g. s3://my-bucket/prefix/)")

    bucket = parts[0]
    prefix = "/".join(parts[1:])
    if prefix and not prefix.endswith("/"):
        prefix = f"{prefix}/"
    return bucket, prefix


def download_player_id_map(
    url: str,
    dest: Path,
    *,
    min_bytes: int = 1_000,
) -> None:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=120) as response:
            status = response.getcode()
            if status != 200:
                raise RuntimeError(f"Download failed: HTTP {status} from {url}")
            data = response.read()
    except HTTPError as exc:
        raise RuntimeError(f"Download failed: HTTP {exc.code} from {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Download failed: {exc.reason}") from exc

    if len(data) < min_bytes:
        raise RuntimeError(
            f"Downloaded file is too small ({len(data)} bytes); expected a CSV from {url}"
        )

    dest.write_bytes(data)


def upload_player_id_map(
    local_path: Path,
    s3_uri: str,
    object_name: str,
) -> str:
    bucket, prefix = _parse_s3_uri(s3_uri)
    key = f"{prefix}{object_name}"
    s3 = boto3.client("s3")
    s3.upload_file(str(local_path), bucket, key)
    return f"s3://{bucket}/{key}"


def run(
    *,
    download_url: str,
    s3_uri: str,
    object_name: str,
    dry_run: bool,
) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        local_path = Path(tmp) / object_name
        print(f"Downloading {download_url} ...")
        download_player_id_map(download_url, local_path)
        print(f"Downloaded {local_path.stat().st_size:,} bytes")

        if dry_run:
            print("Dry run: skipping S3 upload.")
            return ""

        destination = upload_player_id_map(local_path, s3_uri, object_name)
        print(f"Uploaded to {destination}")
        return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Download the Smart Fantasy Baseball (MPD) player ID map CSV and "
            "upload it to S3, overwriting the existing object."
        )
    )
    parser.add_argument(
        "--download-url",
        default=DEFAULT_DOWNLOAD_URL,
        help=f"CSV download URL (default: {DEFAULT_DOWNLOAD_URL})",
    )
    parser.add_argument(
        "--s3-uri",
        default=DEFAULT_S3_URI,
        help=f"Destination S3 prefix (default: {DEFAULT_S3_URI})",
    )
    parser.add_argument(
        "--object-name",
        default=DEFAULT_OBJECT_NAME,
        help=f"S3 object file name (default: {DEFAULT_OBJECT_NAME!r})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Download only; do not upload to S3.",
    )
    args = parser.parse_args(argv)

    try:
        run(
            download_url=args.download_url,
            s3_uri=args.s3_uri,
            object_name=args.object_name,
            dry_run=args.dry_run,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
