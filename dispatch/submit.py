from dataclasses import dataclass

from close.calendar import parse_day
from dispatch.amounts import signed_delta
from dispatch.confirm import confirmation_for_revision, confirm_receipt
from dispatch.outbound import bind_request
from dispatch.pairs import CONFIRMED, confirm_pair, prepare_pair, reject_pair
from dispatch.pending import next_unconfirmed
from meter_core import ConflictError, InputError, transaction
from receiver.pairs import PairMember, post_pair
from receiver.receipts import Receipt
from receiver.requests import post


class ReplyLost(ConnectionError):
    """The receiver's reply did not reach this reporting attempt."""


@dataclass(frozen=True, slots=True)
class AdvanceResult:
    state: str
    seal_id: str | None = None
    receipt: Receipt | None = None


@dataclass(frozen=True, slots=True)
class PairResult:
    state: str
    pair_id: str
    receipts: tuple[Receipt, ...] = ()


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


def advance_pair(local, external, pair_id, seal_ids, *, after_post=None):
    """Run one named paired report covering two different days.

    The receiver books both legs and issues the two first receipts together; the
    local ledger confirms both frontiers only when the complete matching bundle
    is presented. Replays of the same group continue the original case, while
    rebinding the pair id is rejected.
    """
    if local is external:
        raise InputError('reporting requires independent local and external connections')
    status, members = prepare_pair(local, pair_id, seal_ids)
    if status == CONFIRMED:
        return PairResult(
            'confirmed', pair_id, tuple(member.receipt for member in members),
        )

    external_members = [
        PairMember(member.seal_id, member.day, member.delta) for member in members
    ]
    try:
        pair_receipt = post_pair(external, pair_id, external_members)
        if after_post is not None:
            after_post(pair_receipt)
    except ReplyLost:
        return PairResult('pending', pair_id)
    except ConflictError:
        # A member already has its ordinary first posting: the pair cannot use it
        # to sneak the other leg in, and ordinary handling must remain available.
        reject_pair(local, pair_id)
        raise

    confirmed_members = confirm_pair(local, pair_id, pair_receipt)
    return PairResult(
        'confirmed', pair_id, tuple(member.receipt for member in confirmed_members),
    )
