"""Hello-world Prefect flow for the fantasy-baseball platform (ticket #43).

Writes a small stamped text file to:
    s3://dn-lakehouse-dev/_meta/prefect_hello/year=/month=/day=/<timestamp>.txt

This is the "does the whole pipe work end-to-end" smoke test: Prefect schedules
the run, the flow gets AWS credentials, and an object lands in S3. Every real
vendor flow (#44-#47) follows the same shape.

Run locally without AWS or a Prefect API:
    python flows/hello_flow.py --dry-run

Run locally for real (needs AWS creds that can PutObject on the prefix):
    python flows/hello_flow.py

On Prefect Managed compute there are no ambient AWS credentials, so pass the
name of an `AwsCredentials` block (see flows/README.md) and the flow will use
it. Locally, omit it and boto3's default credential chain is used.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from prefect import flow, get_run_logger, task

DEFAULT_S3_BASE = "s3://dn-lakehouse-dev/_meta/prefect_hello"


def _parse_s3_uri(s3_base_path: str) -> tuple[str, str]:
    """Return (bucket, key_prefix) with no leading/trailing slashes on the prefix."""
    if not s3_base_path.startswith("s3://"):
        raise ValueError("S3 path must start with s3://")
    parts = [p for p in s3_base_path[len("s3://"):].split("/") if p]
    if not parts:
        raise ValueError("S3 path must include a bucket (e.g. s3://bucket/prefix)")
    return parts[0], "/".join(parts[1:])


def build_s3_key(base_prefix: str, stamp: datetime) -> str:
    """Build a date-partitioned key: <prefix>/year=/month=/day=/<timestamp>.txt.

    Pure function (no I/O) so it is trivially unit-testable. Matches the
    year=/month=/day= partition convention used by utils/upload_folder_to_s3.py.
    """
    partition = f"year={stamp.year}/month={stamp.month:02d}/day={stamp.day:02d}"
    filename = f"{stamp.strftime('%Y%m%dT%H%M%SZ')}.txt"
    return f"{base_prefix}/{partition}/{filename}" if base_prefix else f"{partition}/{filename}"


def build_stamp_body(stamp: datetime) -> str:
    return (
        "prefect hello-world\n"
        f"written_at_utc={stamp.isoformat()}\n"
        "source=flows/hello_flow.py\n"
    )


def _s3_client(aws_credentials_block: str | None):
    """Return a boto3 S3 client.

    If aws_credentials_block is set, load that Prefect `AwsCredentials` block
    (needed on Prefect Managed compute, which has no ambient AWS creds).
    Otherwise fall back to boto3's default credential chain (local/CI).
    """
    if aws_credentials_block:
        from prefect_aws import AwsCredentials  # lazy: only needed for this path

        return AwsCredentials.load(aws_credentials_block).get_boto3_session().client("s3")

    import boto3  # lazy so --dry-run needs no AWS SDK at import time

    return boto3.client("s3")


@task
def put_object(bucket: str, key: str, body: str, aws_credentials_block: str | None = None) -> str:
    """Upload the stamp to S3 and return the resulting s3:// URI."""
    _s3_client(aws_credentials_block).put_object(
        Bucket=bucket, Key=key, Body=body.encode("utf-8")
    )
    return f"s3://{bucket}/{key}"


@flow(name="hello-world")
def hello_world(
    s3_base_path: str = DEFAULT_S3_BASE,
    aws_credentials_block: str | None = None,
    dry_run: bool = False,
) -> str:
    """Write a stamped file to S3 (or print it, when dry_run=True)."""
    logger = get_run_logger()
    stamp = datetime.now(timezone.utc)
    bucket, base_prefix = _parse_s3_uri(s3_base_path)
    key = build_s3_key(base_prefix, stamp)
    body = build_stamp_body(stamp)

    if dry_run:
        target = f"s3://{bucket}/{key}"
        logger.info("DRY RUN — would write to %s:\n%s", target, body)
        return target

    uri = put_object(bucket, key, body, aws_credentials_block)
    logger.info("Wrote %s", uri)
    return uri


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the hello-world Prefect flow.")
    parser.add_argument(
        "--s3-path",
        default=DEFAULT_S3_BASE,
        help=f"Base S3 URI (default: {DEFAULT_S3_BASE})",
    )
    parser.add_argument(
        "--aws-credentials-block",
        default=None,
        help="Name of a Prefect AwsCredentials block (for Prefect Managed compute).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written instead of calling AWS.",
    )
    args = parser.parse_args()
    print(
        hello_world(
            s3_base_path=args.s3_path,
            aws_credentials_block=args.aws_credentials_block,
            dry_run=args.dry_run,
        )
    )
