import uuid
from datetime import datetime, timezone

import grpc

import klaaryo_pb2
import klaaryo_pb2_grpc
import repository
from event_bus.protocol import EventBus

_CANDIDATE_FIELDS = [
    "external_id",
    "ats_source",
    "first_name",
    "last_name",
    "email",
    "phone",
    "age",
    "job_external_id",
    "internal_status",
    "applied_at",
]


def _row_to_candidate(row) -> klaaryo_pb2.Candidate:
    return klaaryo_pb2.Candidate(
        candidate_pk=row["pk"],
        external_id=row["external_id"] or "",
        ats_source=row["ats_source"] or "",
        first_name=row["first_name"] or "",
        last_name=row["last_name"] or "",
        email=row["email"] or "",
        phone=row["phone"] or "",
        age=row["age"] or 0,
        job_external_id=row["job_external_id"] or "",
        internal_status=row["internal_status"] or "",
        applied_at=row["applied_at"] or "",
    )


class TalentPoolServicer(klaaryo_pb2_grpc.TalentPoolServicer):
    def __init__(self, db_path: str, bus: EventBus) -> None:
        self._db = db_path
        self._bus = bus

    def UpsertCandidate(self, request, context):
        candidate = {f: getattr(request, f) for f in _CANDIDATE_FIELDS}
        pk, created, changed_fields = repository.upsert_candidate(self._db, candidate)

        if created:
            self._emit("candidate.created", pk, request, [])
        elif changed_fields:
            self._emit("candidate.updated", pk, request, changed_fields)
        # no-op (created=False, changed_fields=[]): no event

        return klaaryo_pb2.UpsertResult(
            candidate_pk=pk,
            created=created,
            changed_fields=changed_fields,
        )

    def GetCandidate(self, request, context):
        row = repository.get_candidate(self._db, request.candidate_pk)
        if row is None:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details("Candidate not found")
            return klaaryo_pb2.Candidate()
        return _row_to_candidate(row)

    def ListCandidates(self, request, context):
        rows = repository.list_candidates(
            self._db, request.ats_source or None
        )
        return klaaryo_pb2.ListCandidatesResponse(
            candidates=[_row_to_candidate(r) for r in rows]
        )

    def _emit(
        self,
        event_type: str,
        pk: str,
        request,
        changed_fields: list[str],
    ) -> None:
        self._bus.publish(
            event_type,
            {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "occurred_at": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "candidate_pk": pk,
                "ats_source": request.ats_source,
                "external_id": request.external_id,
                "changed_fields": changed_fields,
            },
        )
