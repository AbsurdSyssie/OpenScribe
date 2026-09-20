"""Shared transaction handling for provider credential writes."""

from contextlib import contextmanager
from collections.abc import Callable, Iterator

from sqlalchemy.orm import Session

from app.models import ProviderSecretCleanupKind
from app.services.provider_secret_cleanup import queue_orphan_provider_secret_after_rollback


@contextmanager
def provider_credential_write_transaction(
    db: Session,
    *,
    kind: ProviderSecretCleanupKind,
) -> Iterator[Callable[[str], None]]:
    """Commit provider-row changes and compensate secrets if that fails.

    The caller registers each Vault reference immediately after its write
    succeeds.  References retired by a successful database change are not
    handled here; they remain the caller's post-change cleanup responsibility.
    """
    written_secret_refs: list[str] = []

    def register_written_secret(secret_ref: str) -> None:
        if secret_ref:
            written_secret_refs.append(secret_ref)

    try:
        yield register_written_secret
        db.commit()
    except Exception:
        db.rollback()
        for secret_ref in written_secret_refs:
            queue_orphan_provider_secret_after_rollback(
                db,
                kind=kind,
                secret_ref=secret_ref,
            )
        raise
