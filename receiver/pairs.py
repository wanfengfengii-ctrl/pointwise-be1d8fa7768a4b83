from dataclasses import dataclass

from close.calendar import parse_day
from meter_core import (
    ConflictError,
    InputError,
    pair_request_id,
    require_litres,
    require_name,
    transaction,
)
from receiver.claims import claim_for_seal
from receiver.ledger import append_posting
from receiver.receipts import Receipt, receipt_for_request, store_receipt

PAIR_KIND = 'pair'


@dataclass(frozen=True, slots=True)
class PairMember:
    seal_id: str
    day: str
    delta: int


@dataclass(frozen=True, slots=True)
class PairReceipt:
    """The receiver-side bundle handed back for one paired report."""
    pair_id: str
    receipts: tuple[Receipt, ...]


def init_pair_schema(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS pair_groups (
            pair_id TEXT NOT NULL,
            seal_id TEXT NOT NULL,
            day TEXT NOT NULL,
            request_id TEXT NOT NULL,
            receipt_id TEXT NOT NULL,
            posting_sequence INTEGER NOT NULL,
            delta TEXT NOT NULL,
            PRIMARY KEY (pair_id, seal_id),
            UNIQUE (seal_id),
            UNIQUE (request_id),
            UNIQUE (receipt_id),
            UNIQUE (posting_sequence)
        )
    ''')


def pair_for_id(connection, pair_id):
    rows = connection.execute('''
        SELECT seal_id, day, request_id, receipt_id, posting_sequence, delta
        FROM pair_groups WHERE pair_id = ? ORDER BY day
    ''', (pair_id,)).fetchall()
    return tuple(
        (seal_id, day, request_id, receipt_id, sequence, int(delta, 16))
        for seal_id, day, request_id, receipt_id, sequence, delta in rows
    )


def _validate_members(members):
    # The receiver is an independent ledger: it never sees sealed revisions, so
    # it validates the request shape and guards ownership with its own tables.
    validated = []
    for member in members:
        if not isinstance(member, PairMember):
            raise InputError('each pair member must carry seal id, day and delta')
        seal_id = require_name(member.seal_id, 'seal_id')
        day = parse_day(member.day)
        delta = require_litres(member.delta)
        validated.append(PairMember(seal_id, day, delta))
    return validated


def post_pair(connection, pair_id, members):
    """Atomically post the two legs of a paired report to the receiver ledger.

    A pair is identified by its pair id and the unordered set of two seal ids.
    Replaying the same group returns the original receipts; rebinding an existing
    pair id, or touching a seal already claimed by any first posting, is rejected.
    """
    pair_id = require_name(pair_id, 'pair_id')
    if not isinstance(members, (list, tuple)) or len(members) != 2:
        raise InputError('a paired report needs exactly two members')
    member_set = {(member.seal_id, member.day, member.delta) for member in members}
    if len(member_set) != 2:
        raise InputError('the two pair members must be distinct seal ids')
    with transaction(connection, write=True):
        existing = pair_for_id(connection, pair_id)
        if existing:
            expected = {(seal_id, day, delta) for seal_id, day, _request, _receipt, _seq, delta in existing}
            if expected != member_set:
                raise ConflictError(f'pair id already binds a different group: {pair_id!r}')
            receipts = tuple(
                receipt_for_request(connection, request_id)
                for _seal, _day, request_id, _receipt, _seq, _delta in existing
            )
            return PairReceipt(pair_id, receipts)

        validated = _validate_members(members)
        days = {member.day for member in validated}
        if len(days) != 2:
            raise InputError('the two pair members must belong to different days')
        if validated[0].delta == 0 or validated[1].delta == 0:
            raise InputError('paired deltas must be nonzero')
        if validated[0].delta != -validated[1].delta:
            raise InputError('paired deltas must be equal and opposite')
        for member in validated:
            claim = claim_for_seal(connection, member.seal_id)
            if claim is not None:
                raise ConflictError(
                    f'seal already has its first posting: {member.seal_id!r}'
                )

        stored_receipts = []
        for member in validated:
            request_id = pair_request_id(pair_id, member.seal_id)
            posting = append_posting(connection, request_id, member.day, member.delta)
            receipt = store_receipt(connection, posting)
            stored_receipts.append(receipt)
            connection.execute(
                'INSERT INTO seal_claims (seal_id, request_id, kind) VALUES (?, ?, ?)',
                (member.seal_id, request_id, PAIR_KIND),
            )
            connection.execute('''
                INSERT INTO pair_groups
                    (pair_id, seal_id, day, request_id, receipt_id, posting_sequence, delta)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                pair_id, member.seal_id, member.day, request_id, receipt.receipt_id,
                receipt.posting_sequence, format(member.delta, 'x'),
            ))
        return PairReceipt(pair_id, tuple(stored_receipts))
