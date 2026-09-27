"""Move unpartitioned NFBC claims CSVs into a date partition (#298)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from nfbc_claims import (  # noqa: E402
    ClaimsUploadError,
    is_unpartitioned_claims_key,
    partitioned_claims_dest_key,
)
from nfbc_claims_repartition import (  # noqa: E402
    delete_unpartitioned_claims_object,
    list_unpartitioned_claims_keys,
    move_unpartitioned_claims,
    parse_args,
)


PREFIX = "nfbc/claims"
WHEN = date(2026, 9, 27)


class FakeS3:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})
        self.copies: list[tuple[str, str]] = []
        self.deletes: list[str] = []

    def list_objects_v2(self, *, Bucket, Prefix="", ContinuationToken=None):
        keys = sorted(key for key in self.objects if key.startswith(Prefix))
        return {"Contents": [{"Key": key} for key in keys], "IsTruncated": False}

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)
        return {"ContentLength": len(self.objects[Key])}

    def copy_object(self, *, Bucket, Key, CopySource):
        source = CopySource["Key"]
        if source not in self.objects:
            raise KeyError(source)
        self.objects[Key] = self.objects[source]
        self.copies.append((source, Key))

    def delete_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)
        del self.objects[Key]
        self.deletes.append(Key)


def test_unpartitioned_key_is_format_folder_plus_claims_csv():
    assert is_unpartitioned_claims_key(
        "nfbc/claims/online_championship/claims_1828.csv", PREFIX
    )
    assert is_unpartitioned_claims_key(
        "nfbc/claims/main_event/claims_1055.csv", PREFIX
    )
    assert not is_unpartitioned_claims_key(
        "nfbc/claims/online_championship/year=2026/month=09/day=27/claims_1828.csv",
        PREFIX,
    )
    assert not is_unpartitioned_claims_key(
        "nfbc/claims/online_championship/notes.txt", PREFIX
    )
    assert not is_unpartitioned_claims_key(
        "nfbc/claims/online_championship/claims_1828.html", PREFIX
    )
    assert not is_unpartitioned_claims_key(
        "nfbc/standings/online_championship/claims_1828.csv", PREFIX
    )
    assert not is_unpartitioned_claims_key(
        "nfbc/claims/qualifier/claims_295.csv", PREFIX
    )


def test_dest_key_inserts_partition_under_existing_format_folder():
    assert partitioned_claims_dest_key(
        "nfbc/claims/online_championship/claims_1828.csv", when=WHEN
    ) == (
        "nfbc/claims/online_championship/year=2026/month=09/day=27/claims_1828.csv"
    )
    assert partitioned_claims_dest_key(
        "nfbc/claims/main_event/claims_1055.csv", when=WHEN
    ) == ("nfbc/claims/main_event/year=2026/month=09/day=27/claims_1055.csv")


def test_list_skips_partitioned_and_non_claims_objects():
    client = FakeS3(
        {
            "nfbc/claims/online_championship/claims_1828.csv": b"oc",
            "nfbc/claims/main_event/claims_1055.csv": b"me",
            "nfbc/claims/online_championship/year=2026/month=09/day=26/claims_217.csv": b"old",
            "nfbc/claims/online_championship/notes.txt": b"no",
            "nfbc/claims/online_championship/claims_1828.html": b"html",
        }
    )
    assert list_unpartitioned_claims_keys(client, "dn-lakehouse-dev", PREFIX) == [
        "nfbc/claims/main_event/claims_1055.csv",
        "nfbc/claims/online_championship/claims_1828.csv",
    ]


def test_move_copies_into_today_partition_and_deletes_only_sources():
    already = (
        "nfbc/claims/online_championship/year=2026/month=09/day=26/claims_217.csv"
    )
    neighbor = "nfbc/claims/online_championship/notes.txt"
    client = FakeS3(
        {
            "nfbc/claims/online_championship/claims_1828.csv": b"oc-1828",
            "nfbc/claims/main_event/claims_1055.csv": b"me-1055",
            already: b"keep-partitioned",
            neighbor: b"keep-notes",
        }
    )
    rc = move_unpartitioned_claims(
        s3_base="s3://dn-lakehouse-dev/nfbc/claims",
        when=WHEN,
        dry_run=False,
        s3_client=client,
    )
    assert rc == 0
    assert client.objects == {
        "nfbc/claims/online_championship/year=2026/month=09/day=27/claims_1828.csv": b"oc-1828",
        "nfbc/claims/main_event/year=2026/month=09/day=27/claims_1055.csv": b"me-1055",
        already: b"keep-partitioned",
        neighbor: b"keep-notes",
    }
    assert "nfbc/claims/online_championship/claims_1828.csv" not in client.objects
    assert "nfbc/claims/main_event/claims_1055.csv" not in client.objects
    assert already not in client.deletes
    assert neighbor not in client.deletes


def test_dry_run_does_not_copy_or_delete():
    client = FakeS3({"nfbc/claims/online_championship/claims_1828.csv": b"oc"})
    rc = move_unpartitioned_claims(
        s3_base="s3://dn-lakehouse-dev/nfbc/claims",
        when=WHEN,
        dry_run=True,
        s3_client=client,
    )
    assert rc == 0
    assert client.copies == []
    assert client.deletes == []
    assert "nfbc/claims/online_championship/claims_1828.csv" in client.objects


def test_existing_destination_is_skipped_and_source_kept():
    dest = "nfbc/claims/online_championship/year=2026/month=09/day=27/claims_1828.csv"
    client = FakeS3(
        {
            "nfbc/claims/online_championship/claims_1828.csv": b"new",
            dest: b"already",
        }
    )
    rc = move_unpartitioned_claims(
        s3_base="s3://dn-lakehouse-dev/nfbc/claims",
        when=WHEN,
        s3_client=client,
    )
    assert rc == 0
    assert client.objects[dest] == b"already"
    assert client.objects["nfbc/claims/online_championship/claims_1828.csv"] == b"new"
    assert client.deletes == []


def test_failed_copy_keeps_source():
    class BrokenCopy(FakeS3):
        def copy_object(self, **kwargs):
            raise RuntimeError("copy failed")

    client = BrokenCopy({"nfbc/claims/online_championship/claims_1828.csv": b"oc"})
    rc = move_unpartitioned_claims(
        s3_base="s3://dn-lakehouse-dev/nfbc/claims",
        when=WHEN,
        s3_client=client,
    )
    assert rc == 1
    assert "nfbc/claims/online_championship/claims_1828.csv" in client.objects
    assert client.deletes == []


def test_delete_refuses_partitioned_or_other_keys():
    client = FakeS3(
        {
            "nfbc/claims/online_championship/year=2026/month=09/day=27/claims_1828.csv": b"x",
            "nfbc/claims/online_championship/notes.txt": b"y",
        }
    )
    with pytest.raises(ClaimsUploadError, match="Refusing to delete"):
        delete_unpartitioned_claims_object(
            client,
            "dn-lakehouse-dev",
            "nfbc/claims/online_championship/year=2026/month=09/day=27/claims_1828.csv",
            PREFIX,
        )
    with pytest.raises(ClaimsUploadError, match="Refusing to delete"):
        delete_unpartitioned_claims_object(
            client,
            "dn-lakehouse-dev",
            "nfbc/claims/online_championship/notes.txt",
            PREFIX,
        )
    assert len(client.objects) == 2


def test_cli_defaults_to_claims_prefix():
    args = parse_args(["--dry-run", "--date", "2026-09-27"])
    assert args.s3_base == "s3://dn-lakehouse-dev/nfbc/claims"
    assert args.dry_run is True
    assert args.date == "2026-09-27"
