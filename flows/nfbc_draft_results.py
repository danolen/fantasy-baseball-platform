"""NFBC draft-results Prefect flow (ticket #299).

Cookie POSTs to ``/draft_results.data.php`` (login-gated, not Cloudflare-
challenged in the 2026 spike). League ids for this season come from the
public draft-results ``#league_id`` dropdown (60 ME + 240 OC). How to
discover **next** season's league_ids is a follow-up.

Snake-draft pick log only. Auction-equivalent dollars of the pick slot are
a later dbt join (#301), not this ingest.

Not registered in ``prefect.yaml``: Hobby is at the 5-deployment cap. Run
locally, or add a named deploy when draft season starts.

    python flows/nfbc_draft_results.py --dry-run
    python flows/nfbc_draft_results.py
    python flows/nfbc_draft_results.py --league-id 1828
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

from prefect import flow, get_run_logger

_FLOWS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _FLOWS_DIR.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "nfbc_draft_results.py"
if str(_FLOWS_DIR) not in sys.path:
    sys.path.insert(0, str(_FLOWS_DIR))

from hello_flow import _s3_client  # noqa: E402


def _load_draft_script():
    """Load ``scripts/nfbc_draft_results.py`` under a unique module name.

    This file and the script share a basename, so a normal import would
    collide when Prefect loads the flow as ``nfbc_draft_results``.
    """
    spec = importlib.util.spec_from_file_location(
        "nfbc_draft_results_script", _SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load draft-results script from {_SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_draft = _load_draft_script()
DEFAULT_S3_BASE = _draft.DEFAULT_S3_BASE
DEFAULT_FORMAT = _draft.DEFAULT_FORMAT
DEFAULT_SEASON = _draft.DEFAULT_SEASON
DEFAULT_PAUSE_SECONDS = _draft.DEFAULT_PAUSE_SECONDS
DATA_URL = _draft.DATA_URL
INDEX_URL = _draft.INDEX_URL
DEFAULT_SECRET_NAME = "fantasy-baseball-platform"
DEFAULT_NFBC_LIU_KEY = "nfbc_liu"
DEFAULT_NFBC_JWT_KEY = "nfbc_jwt"


def _default_secret_region() -> str:
    """Same AWS region as the other vendor flows (env, else fangraphs_ros)."""
    env = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION")
    if env:
        return env
    sibling = _FLOWS_DIR / "fangraphs_ros.py"
    for line in sibling.read_text(encoding="utf-8").splitlines():
        if line.startswith("DEFAULT_SECRET_REGION ="):
            import ast

            return ast.literal_eval(line.split("=", 1)[1].strip())
    raise RuntimeError(
        "Set --secret-region or AWS_DEFAULT_REGION (same region as other vendor flows)."
    )


DEFAULT_SECRET_REGION = _default_secret_region()


def _boto3_session(aws_credentials_block: str | None):
    if aws_credentials_block:
        from prefect_aws import AwsCredentials

        return AwsCredentials.load(aws_credentials_block).get_boto3_session()

    import boto3

    return boto3.Session()


def fetch_secret_json(
    secret_name: str,
    *,
    region: str,
    aws_credentials_block: str | None = None,
) -> dict:
    client = _boto3_session(aws_credentials_block).client(
        "secretsmanager", region_name=region
    )
    response = client.get_secret_value(SecretId=secret_name)
    raw = response.get("SecretString")
    if not raw:
        raise ValueError(f"Secret {secret_name} has no SecretString payload")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"Secret {secret_name} must be a JSON object")
    return payload


def fetch_nfbc_cookies(
    *,
    secret_name: str,
    secret_region: str,
    liu_key: str = DEFAULT_NFBC_LIU_KEY,
    jwt_key: str = DEFAULT_NFBC_JWT_KEY,
    aws_credentials_block: str | None = None,
) -> tuple[str, str | None]:
    """Load NFBC session cookie values from Secrets Manager (values only)."""
    payload = fetch_secret_json(
        secret_name, region=secret_region, aws_credentials_block=aws_credentials_block
    )
    liu = str(payload.get(liu_key) or "").strip()
    jwt = str(payload.get(jwt_key) or "").strip() or None
    if not liu:
        raise ValueError(
            f"Secret {secret_name} is missing key {liu_key!r} for NFBC auth"
        )
    return liu, jwt


def _apply_nfbc_env(liu: str, jwt: str | None) -> None:
    os.environ["NFBC_LIU"] = liu
    if jwt:
        os.environ["NFBC_JWT"] = jwt
    elif "NFBC_JWT" in os.environ:
        del os.environ["NFBC_JWT"]


def _dry_run_summary(
    *,
    s3_base: str,
    league_id: int | None,
    format: str,
) -> dict:
    partition = _draft.date_partition_path()
    summary = {
        "dry_run": True,
        "s3_base": s3_base,
        "partition": partition,
        "index_url": INDEX_URL,
        "data_url": DATA_URL,
        "league_id": league_id,
        "format": format,
    }
    if league_id is not None:
        summary["s3_uri"] = _draft.draft_s3_uri(s3_base, format, league_id)
    return summary


@flow(name="nfbc-draft-results")
def nfbc_draft_results(
    s3_base: str = DEFAULT_S3_BASE,
    secret_name: str = DEFAULT_SECRET_NAME,
    secret_region: str = DEFAULT_SECRET_REGION,
    aws_credentials_block: str | None = None,
    league_id: int | None = None,
    format: str = DEFAULT_FORMAT,
    season: int = DEFAULT_SEASON,
    keep_local: bool = False,
    pause_seconds: float = DEFAULT_PAUSE_SECONDS,
    dry_run: bool = False,
) -> dict:
    """Capture 2026 ME/OC snake-draft results to partitioned S3."""
    logger = get_run_logger()
    if dry_run:
        summary = _dry_run_summary(
            s3_base=s3_base, league_id=league_id, format=format
        )
        if league_id is not None:
            logger.info(
                "DRY RUN — would POST %s for league_id=%s format=%s with "
                "Secrets Manager nfbc_liu/nfbc_jwt and upload %s. "
                "Auction $ is not in this ingest.",
                DATA_URL,
                league_id,
                format,
                summary["s3_uri"],
            )
        else:
            logger.info(
                "DRY RUN — would GET %s for this season's ME/OC league_ids, "
                "POST each to %s with Secrets Manager nfbc_liu/nfbc_jwt, "
                "upload to %s/{format}/%s/draft_{id}.csv, then delete "
                "local draft_{id}.csv. Next-season league discovery is a "
                "follow-up. Auction $ is not in this ingest.",
                INDEX_URL,
                DATA_URL,
                s3_base,
                _draft.date_partition_path(),
            )
        return summary

    liu, jwt = fetch_nfbc_cookies(
        secret_name=secret_name,
        secret_region=secret_region,
        aws_credentials_block=aws_credentials_block,
    )
    _apply_nfbc_env(liu, jwt)
    s3_client = _s3_client(aws_credentials_block)

    try:
        if league_id is None:
            argv = [
                "--all",
                "--s3",
                "--s3-base",
                s3_base,
                "--season",
                str(season),
                "--pause-seconds",
                str(pause_seconds),
            ]
            if keep_local:
                argv.append("--keep-local")
            rc = _draft.run_all_leagues(
                _draft.parse_args(argv), s3_client=s3_client
            )
            if rc != 0:
                raise _draft.DraftFetchError(
                    "One or more draft-results leagues failed; see stderr."
                )
            return {"s3_base": s3_base, "all": True, "season": season}

        html = _draft.fetch_draft_results_html(league_id, liu=liu, jwt=jwt)
        uri = _draft.process_league(
            html,
            league=_draft.DraftLeague(
                league_id=league_id,
                format=format,
                label=f"league {league_id}",
            ),
            season=season,
            output=_draft.default_output_path(league_id),
            s3=True,
            s3_base=s3_base,
            keep_local=keep_local,
            s3_client=s3_client,
        )
        return {"s3_uri": uri, "league_id": league_id, "format": format}
    except (
        _draft.DraftParseError,
        _draft.DraftFetchError,
        _draft.DraftUploadError,
    ):
        logger.exception("NFBC draft-results ingest failed")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Run the NFBC draft-results Prefect flow. Not registered in "
            "prefect.yaml (Hobby 5-deployment cap)."
        )
    )
    parser.add_argument(
        "--s3-base",
        default=DEFAULT_S3_BASE,
        help=f"S3 prefix (default: {DEFAULT_S3_BASE})",
    )
    parser.add_argument(
        "--secret-name",
        default=DEFAULT_SECRET_NAME,
        help=f"AWS Secrets Manager secret name (default: {DEFAULT_SECRET_NAME})",
    )
    parser.add_argument(
        "--secret-region",
        default=DEFAULT_SECRET_REGION,
        help=f"AWS region for the secret (default: {DEFAULT_SECRET_REGION})",
    )
    parser.add_argument(
        "--aws-credentials-block",
        default=None,
        help="Name of a Prefect AwsCredentials block (for Prefect Managed compute).",
    )
    parser.add_argument(
        "--league-id",
        type=int,
        default=None,
        help="Capture one league instead of every ME/OC id on the dropdown.",
    )
    parser.add_argument(
        "--format",
        default=DEFAULT_FORMAT,
        help=f"Contest format when using --league-id (default: {DEFAULT_FORMAT}).",
    )
    parser.add_argument(
        "--season",
        type=int,
        default=DEFAULT_SEASON,
        help=f"Season year (default: {DEFAULT_SEASON}).",
    )
    parser.add_argument(
        "--keep-local",
        action="store_true",
        help="After a successful S3 upload, keep the local draft CSV.",
    )
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=DEFAULT_PAUSE_SECONDS,
        help="Sleep between leagues when capturing --all (default: 1).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned fetches/uploads instead of calling NFBC/AWS.",
    )
    args = parser.parse_args()
    print(
        nfbc_draft_results(
            s3_base=args.s3_base,
            secret_name=args.secret_name,
            secret_region=args.secret_region,
            aws_credentials_block=args.aws_credentials_block,
            league_id=args.league_id,
            format=args.format,
            season=args.season,
            keep_local=args.keep_local,
            pause_seconds=args.pause_seconds,
            dry_run=args.dry_run,
        )
    )
