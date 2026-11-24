from dataclasses import dataclass

from close.calendar import parse_day
from dispatch.amounts import signed_delta
from dispatch.confirm import confirmation_for_revision, confirm_receipt
from dispatch.outbound import bind_request
from dispatch.pending import next_unconfirmed
from meter_core import InputError, transaction
from receiver.receipts import Receipt
from receiver.requests import post


class ReplyLost(ConnectionError):
    """The receiver's reply did not reach this reporting attempt."""


@dataclass(frozen=True, slots=True)
class AdvanceResult:
    state: str
    seal_id: str | None = None
    receipt: Receipt | None = None


def _prepare(connection, day):
    with transaction(connection, write=True):
        pending = next_unconfirmed(connection, day)
        if pending is None:
            return None
        if pending.request is not None:
            return pending.request
        delta = signed_delta(pending.revision.total, pending.previous_total)
        return bind_request(connection, pending.revision, delta)


def advance(local, external, day, *, after_post=None):
    day = parse_day(day)
    if local is external:
        raise InputError('reporting requires independent local and external connections')
    request = _prepare(local, day)
    if request is None:
        return AdvanceResult('idle')

    try:
        receipt = post(external, request.request_id, request.day, request.delta)
        if after_post is not None:
            after_post(receipt)
    except ReplyLost:
        # Another worker may have confirmed this same request in the meantime.
        existing = confirmation_for_revision(local, request.seal_id)
        if existing is None:
            return AdvanceResult('pending', request.seal_id)
        return AdvanceResult('confirmed', request.seal_id, existing)

    confirmed = confirm_receipt(local, request, receipt)
    return AdvanceResult('confirmed', request.seal_id, confirmed)
