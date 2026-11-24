from dataclasses import dataclass

from close.calendar import parse_day
from close.revisions import latest_revision
from intake.source_detail import list_source_keys


@dataclass(frozen=True, slots=True)
class CoveredSource:
    batch_id: str
    covering_seal_id: str


def eligible_source_batches(connection, cutoff):
    cutoff = parse_day(cutoff)
    candidates = []
    coverage_by_day = {}
    for source in list_source_keys(connection, cutoff):
        if source.day not in coverage_by_day:
            coverage_by_day[source.day] = latest_revision(connection, source.day)
        covering = coverage_by_day[source.day]
        if covering is not None and source.sequence <= covering.source_watermark:
            candidates.append(CoveredSource(source.batch_id, covering.seal_id))
    return tuple(candidates)
