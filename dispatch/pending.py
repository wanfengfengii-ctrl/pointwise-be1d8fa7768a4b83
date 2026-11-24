from dataclasses import dataclass

from close.revisions import Revision, get_revision_at, next_revision
from dispatch.confirm import confirmation_for_revision, confirmation_frontier
from dispatch.outbound import OutboundRequest, request_for_revision
from meter_core import EvidenceError


@dataclass(frozen=True, slots=True)
class PendingRevision:
    revision: Revision
    request: OutboundRequest | None
    previous_total: int


def next_unconfirmed(connection, day):
    frontier = confirmation_frontier(connection, day)
    revision = next_revision(connection, day, frontier)
    if revision is None:
        return None
    previous_total = 0
    if frontier:
        previous = get_revision_at(connection, day, frontier)
        if previous is None or confirmation_for_revision(connection, previous.seal_id) is None:
            raise EvidenceError('confirmation frontier lacks its receipt')
        previous_total = previous.total
    request = request_for_revision(connection, revision.seal_id)
    return PendingRevision(revision, request, previous_total)
