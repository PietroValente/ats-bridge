import logging
from datetime import datetime, timedelta, timezone

from adapters import REGISTRY
from adapters.protocol import NormalizedApplication
from db_models.models import SyncState
import utils.grpc_client as grpc_client

logger = logging.getLogger(__name__)

_VALID_STATUSES = {"new", "in_review", "rejected", "hired"}

# Spec default: on the first sync (no watermark yet) look back 7 days.
# Fixtures are dated within this window (last 24h) so the initial sync pulls them all.
_INITIAL_LOOKBACK = timedelta(days=7)


def _validate(n: NormalizedApplication) -> str | None:
    """Return the skip reason, or None if the record is valid."""
    if not n.email:
        return "missing_email"
    if n.age < 18:
        return "minor_candidate"
    if n.internal_status not in _VALID_STATUSES:
        return "invalid_status"
    return None


class SyncManager:
    def run_sync(self, ats_source: str) -> dict:
        adapter = REGISTRY.get(ats_source)
        if adapter is None:
            raise ValueError(f"Unknown ATS source: {ats_source!r}")

        state, _ = SyncState.objects.get_or_create(ats_source=ats_source)
        since = state.last_sync_at or (datetime.now(timezone.utc) - _INITIAL_LOOKBACK)

        raw_list = adapter.fetch_applications(since)

        pushed = 0
        skipped_reasons: dict[str, int] = {}

        for raw in raw_list:
            try:
                normalized = adapter.normalize(raw)
            except Exception:
                reason = "normalization_error"
                skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
                continue

            reason = _validate(normalized)
            if reason:
                skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
                continue

            try:
                grpc_client.upsert_candidate(normalized)
                pushed += 1
            except Exception as exc:
                logger.error(
                    "gRPC UpsertCandidate failed for %s/%s: %s",
                    normalized.ats_source,
                    normalized.external_id,
                    exc,
                )
                skipped_reasons["grpc_error"] = skipped_reasons.get("grpc_error", 0) + 1

        skipped = sum(skipped_reasons.values())
        state.last_sync_at = datetime.now(timezone.utc)
        state.total_pushed += pushed
        state.total_skipped += skipped
        state.save()

        return {
            "pulled": len(raw_list),
            "pushed": pushed,
            "skipped": skipped,
            "skipped_reasons": skipped_reasons,
        }

    def get_status(self, ats_source: str) -> dict:
        if ats_source not in REGISTRY:
            raise ValueError(f"Unknown ATS source: {ats_source!r}")
        try:
            state = SyncState.objects.get(ats_source=ats_source)
            return {
                "last_sync_at": state.last_sync_at.isoformat() if state.last_sync_at else None,
                "total_pushed": state.total_pushed,
                "total_skipped": state.total_skipped,
            }
        except SyncState.DoesNotExist:
            return {"last_sync_at": None, "total_pushed": 0, "total_skipped": 0}
