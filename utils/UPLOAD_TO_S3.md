# Upload files to S3

Small helper script: `upload_folder_to_s3.py` uploads **one file** or **every regular file in a folder** (not subfolders) to Amazon S3. Objects land under your chosen prefix plus a date partition: `year=YYYY/month=MM/day=DD/`.

## Prerequisites

- Python 3 installed
- Dependencies: from the repo root, run `pip install -r requirements.txt` (includes `boto3`)
- AWS credentials available to boto3 (for example `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`, `AWS_PROFILE`, or the default profile in `~/.aws/credentials` on macOS/Linux or `%UserProfile%\.aws\credentials` on Windows)

## Command (all operating systems)

Use the same invocation everywhere; only how you write **local paths** changes.

```bash
python utils/upload_folder_to_s3.py --local-path "<LOCAL_FILE_OR_FOLDER>" --s3-path "s3://<BUCKET>/<OPTIONAL_PREFIX>"
```

Run this from the **repository root** so `utils/upload_folder_to_s3.py` resolves correctly. If you run from another directory, use the full path to the script instead of `utils/...`.

### Local path tips

| Situation | What to use |
|-----------|----------------|
| Path has spaces | Keep the quotes around `--local-path` as shown above |
| macOS / Linux | Forward slashes are fine: `./data`, `/home/user/file.csv` |
| Windows Command Prompt | Backslashes or forward slashes usually work; quotes if needed: `"C:\Users\you\data"` or `"C:/Users/you/data"` |
| Windows PowerShell | Same as above; e.g. `.\data\file.csv` or `"C:\path with spaces\file.csv"` |

### Examples

Upload every file in a folder:

```bash
python utils/upload_folder_to_s3.py --local-path "./my_data" --s3-path "s3://my-bucket/raw/ingest"
```

Upload a single file:

```bash
python utils/upload_folder_to_s3.py --local-path "./exports/report.csv" --s3-path "s3://my-bucket/raw/ingest"
```

See all options:

```bash
python utils/upload_folder_to_s3.py --help
```

## Resulting S3 layout

For `--s3-path s3://my-bucket/raw/ingest` and upload date 2025-03-22, keys look like:

- `raw/ingest/year=2025/month=03/day=22/<filename>`

If you omit the prefix and use `s3://my-bucket`, keys start at `year=2025/month=03/day=22/<filename>`.
