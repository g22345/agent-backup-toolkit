from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_backup_toolkit.errors import VerifyError
from agent_backup_toolkit.receipts import (
    FinalReceipt,
    PreparedReceipt,
    canonical_receipt_bytes,
    finalize_receipt,
    parse_receipt,
    validate_final_matches,
)

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
BACKUP_ID = "12345678-1234-4234-8234-123456789abc"


def prepared_receipt() -> PreparedReceipt:
    return PreparedReceipt(
        backup_id=BACKUP_ID,
        started_at=datetime(2026, 7, 17, tzinfo=UTC),
        source_names=("notes",),
        file_count=1,
        total_bytes=4,
        destination_type="local",
        artifact_filename=f"{BACKUP_ID}.tar.gz.age",
        artifact_bytes=100,
        artifact_sha256=DIGEST_A,
        manifest_sha256=DIGEST_B,
    )


def test_prepared_to_final_preserves_identity_and_artifact() -> None:
    prepared = prepared_receipt()
    final = finalize_receipt(prepared, completed_at=datetime(2026, 7, 17, 1, tzinfo=UTC))

    validate_final_matches(prepared, final)
    parsed = parse_receipt(canonical_receipt_bytes(final))
    assert isinstance(parsed, FinalReceipt)
    assert parsed.backup_id == prepared.backup_id
    assert parsed.artifact_sha256 == prepared.artifact_sha256


def test_mismatched_final_receipt_is_rejected() -> None:
    prepared = prepared_receipt()
    final = finalize_receipt(prepared).model_copy(update={"artifact_sha256": "c" * 64})

    with pytest.raises(VerifyError, match="does not match"):
        validate_final_matches(prepared, final)


def test_receipt_rejects_unknown_or_private_fields() -> None:
    payload = prepared_receipt().model_dump(mode="json")
    payload["source_path"] = "/private/operator/path"

    with pytest.raises(VerifyError, match="validation failed"):
        parse_receipt(__import__("json").dumps(payload).encode())


def test_artifact_filename_must_match_backup_id() -> None:
    payload = prepared_receipt().model_dump()
    payload["artifact_filename"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.tar.gz.age"

    with pytest.raises(ValueError, match="must match backup_id"):
        PreparedReceipt.model_validate(payload)
