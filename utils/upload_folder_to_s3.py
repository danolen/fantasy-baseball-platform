import argparse
import os
from datetime import date
import boto3


def upload_folder_to_s3(local_path: str, s3_base_path: str):
    """
    Upload all files from a local folder to an S3 path with
    year/month/day partitions based on today's date.
    """

    if not os.path.isdir(local_path):
        raise ValueError(f"Local path does not exist or is not a directory: {local_path}")

    # Parse S3 bucket and prefix
    if not s3_base_path.startswith("s3://"):
        raise ValueError("S3 path must start with s3://")

    s3_path = s3_base_path.replace("s3://", "")
    bucket, *prefix_parts = s3_path.split("/")
    base_prefix = "/".join(prefix_parts)

    # Build partition path
    today = date.today()
    partition_path = (
        f"year={today.year}/"
        f"month={today.month:02d}/"
        f"day={today.day:02d}"
    )

    full_prefix = f"{base_prefix}/{partition_path}".strip("/")

    s3 = boto3.client("s3")

    for filename in os.listdir(local_path):
        local_file = os.path.join(local_path, filename)

        if not os.path.isfile(local_file):
            continue

        s3_key = f"{full_prefix}/{filename}"

        print(f"Uploading {local_file} -> s3://{bucket}/{s3_key}")
        s3.upload_file(local_file, bucket, s3_key)

    print("Upload complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Upload all files from a local folder to S3 with date partitions"
    )
    parser.add_argument(
        "--local-path",
        required=True,
        help="Local folder containing files to upload"
    )
    parser.add_argument(
        "--s3-path",
        required=True,
        help="Base S3 path (e.g. s3://bucket/folder/path)"
    )

    args = parser.parse_args()

    upload_folder_to_s3(
        local_path=args.local_path,
        s3_base_path=args.s3_path
    )
