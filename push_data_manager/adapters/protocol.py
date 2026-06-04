from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class NormalizedApplication:
    external_id: str
    ats_source: str
    first_name: str
    last_name: str
    email: str
    phone: str
    age: int
    job_external_id: str
    internal_status: str
    applied_at: str


class ATSAdapter(Protocol):
    def fetch_applications(self, since: datetime) -> list[dict]: ...
    def normalize(self, raw: dict) -> NormalizedApplication: ...
