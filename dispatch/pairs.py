from dataclasses import dataclass

from close.revisions import get_revision, get_revision_at, next_revision
from dispatch.confirm import confirmation_frontier
from dispatch.amounts import signed_delta
from meter_core import (
    ConflictError,
    EvidenceError,
    InputError,
    decode_litres,
    encode_litres,
    pair_request_id,
    require_name,
    transaction,
)
from receiver.receipts import Receipt

PENDING = 'pending'
CONFIRMED = 'confirmed'
REJECTED = 'rejected'
LIVING = 1


@dataclass(frozen=True, slots=True)
class PairMemberRecord:
    seal_id: str
    day: str
    sequence: int
    delta: int
    receipt: Receipt | None = None


def init_pair_report_schema(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS paired_reports (
            pair_id TEXT PRIMARY KEY NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN ('pending', 'confirmed', 'rejected'))
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS paired_members (
            pair_id TEXT NOT NULL REFERENCES paired_reports (pair_id),
            seal_id TEXT NOT NULL REFERENCES revisions (seal_id),
            day TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            delta TEXT NOT NULL,
            receipt_id TEXT,
            request_id TEXT,
            posting_sequence INTEGER,
            living INTEGER NOT NULL CHECK (living IN (0, 1)),
            PRIMARY KEY (pair_id, seal_id)
        )
    ''')
    # A seal can belong to at most one living pair; rejected rows stay on file
    # so rebinding the original pair id can still be detected.
    connection.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS paired_member_living_seal
            ON paired_members (seal_id) WHERE living = 1
    ''')


def pair_status(connection, pair_id):
    row = connection.execute(
        'SELECT status FROM paired_reports WHERE pair_id = ?', (pair_id,),
    ).fetchone()
    return None if row is None else row[0]


def load_pair(connection, pair_id):
    rows = connection.execute('''
        SELECT seal_id, day, sequence, delta, receipt_id, request_id, posting_sequence
        FROM paired_members WHERE pair_id = ? ORDER BY day
    ''', (pair_id,)).fetchall()
    members = []
    for seal_id, day, sequence, delta, receipt_id, request_id, posting_sequence in rows:
        receipt = None
        if receipt_id is not None:
            receipt = Receipt(receipt_id, request_id, day, decode_litres(delta), posting_sequence)
        members.append(PairMemberRecord(seal_id, day, sequence, decode_litres(delta), receipt))
    return tuple(members)


def _normalise_seal_ids(seal_ids):
    if not isinstance(seal_ids, (list, tuple)):
        raise InputError('seal_ids must be a list or tuple of two seal ids')
    normalised = tuple(seal_ids)
    if len(normalised) != 2:
        raise InputError('a paired report needs exactly two seal ids')
    for seal_id in normalised:
        require_name(seal_id, 'seal_id')
    if normalised[0] == normalised[1]:
        raise InputError('the two pair members must be distinct seal ids')
    return normalised


def prepare_pair(connection, pair_id, seal_ids):
    """Validate and bind a paired report against the local confirmation frontiers.

    Each seal must be the earliest unconfirmed revision of its own day, and the
    net delta against the adjacent confirmed revision must be nonzero, equal and
    opposite to the other leg. At most one unresolved pair is allowed per day.
    The pair is identified by its id and the unordered set of two seal ids:
    same-group retries continue the original case, while rebinding is rejected.
    """
    pair_id = require_name(pair_id, 'pair_id')
    normalised = _normalise_seal_ids(seal_ids)
    requested = frozenset(normalised)
    with transaction(connection, write=True):
        status = pair_status(connection, pair_id)
        if status is not None:
            members = load_pair(connection, pair_id)
            if frozenset(member.seal_id for member in members) != requested:
                raise ConflictError(f'pair id already binds a different group: {pair_id!r}')
            if status == CONFIRMED:
                return CONFIRMED, members
            if status == PENDING:
                return PENDING, members
            # A rejected same-group case may be reopened and continued.
            connection.execute(
                'UPDATE paired_reports SET status = ? WHERE pair_id = ?', (PENDING, pair_id),
            )
            connection.execute(
                'UPDATE paired_members SET living = 1 WHERE pair_id = ?', (pair_id,),
            )
            return PENDING, load_pair(connection, pair_id)

        members = []
        revisions = []
        for seal_id in normalised:
            revision = get_revision(connection, seal_id)
            if revision is None:
                raise ConflictError(f'unknown sealed revision: {seal_id!r}')
            revisions.append(revision)
        if revisions[0].day == revisions[1].day:
            raise InputError('the two pair members must belong to different days')
        for revision in revisions:
            seal_id = revision.seal_id
            frontier = confirmation_frontier(connection, revision.day)
            head = next_revision(connection, revision.day, frontier)
            if head is None or head.seal_id != seal_id:
                raise ConflictError(
                    f'seal is not the first unconfirmed revision: {seal_id!r}'
                )
            previous_total = 0
            if frontier:
                previous = get_revision_at(connection, revision.day, frontier)
                if previous is None:
                    raise ConflictError('confirmation frontier lacks its sealed revision')
                previous_total = previous.total
            members.append(PairMemberRecord(
                revision.seal_id, revision.day, revision.sequence,
                signed_delta(revision.total, previous_total),
            ))

        days = {member.day for member in members}
        if len(days) != 2:
            raise InputError('the two pair members must belong to different days')
        if members[0].delta == 0 or members[1].delta == 0:
            raise InputError('paired deltas must be nonzero')
        if members[0].delta != -members[1].delta:
            raise InputError('paired deltas must be equal and opposite')

        placeholders = ','.join('?' for _ in members)
        blocked = connection.execute(f'''
            SELECT pm.day
            FROM paired_members AS pm
            JOIN paired_reports AS pr ON pr.pair_id = pm.pair_id
            WHERE pr.status = ? AND pm.living = 1 AND pm.day IN ({placeholders})
        ''', (PENDING, *(member.day for member in members))).fetchall()
        if blocked:
            raise ConflictError(
                f'another unresolved paired report already covers: {sorted(day for (day,) in blocked)!r}'
            )

        connection.execute(
            'INSERT INTO paired_reports (pair_id, status) VALUES (?, ?)', (pair_id, PENDING),
        )
        for member in members:
            connection.execute('''
                INSERT INTO paired_members
                    (pair_id, seal_id, day, sequence, delta,
                     receipt_id, request_id, posting_sequence, living)
                VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, ?)
            ''', (
                pair_id, member.seal_id, member.day, member.sequence,
                encode_litres(member.delta), LIVING,
            ))
        return PENDING, load_pair(connection, pair_id)


def reject_pair(connection, pair_id):
    """Release a pending pair after it lost first-posting ownership externally.

    The rows are retained (living = 0) to remember the original id-to-group
    binding, while the days regain their ordinary reporting eligibility.
    """
    with transaction(connection, write=True):
        if pair_status(connection, pair_id) == PENDING:
            connection.execute(
                'UPDATE paired_reports SET status = ? WHERE pair_id = ?', (REJECTED, pair_id),
            )
            connection.execute(
                'UPDATE paired_members SET living = 0 WHERE pair_id = ?', (pair_id,),
            )


def confirmed_receipt_for_seal(connection, seal_id):
    row = connection.execute('''
        SELECT receipt_id, request_id, day, delta, posting_sequence
        FROM paired_members
        WHERE seal_id = ? AND living = 1 AND receipt_id IS NOT NULL
    ''', (seal_id,)).fetchone()
    if row is None:
        return None
    receipt_id, request_id, day, delta, sequence = row
    return Receipt(receipt_id, request_id, day, decode_litres(delta), sequence)


def confirm_pair(connection, pair_id, pair_receipt):
    """Jointly confirm both days only against the complete matching receiver bundle."""
    pair_id = require_name(pair_id, 'pair_id')
    with transaction(connection, write=True):
        if pair_status(connection, pair_id) != PENDING:
            raise ConflictError(f'paired report is not pending: {pair_id!r}')
        members = load_pair(connection, pair_id)
        if len(members) != 2 or pair_receipt.pair_id != pair_id:
            raise EvidenceError('pair receipt does not identify this paired report')
        receipts = tuple(pair_receipt.receipts)
        if len(receipts) != 2:
            raise EvidenceError('pair receipt must contain exactly two receipts')
        by_request = {receipt.request_id: receipt for receipt in receipts}
        expected_requests = {pair_request_id(pair_id, member.seal_id) for member in members}
        if set(by_request) != expected_requests:
            raise EvidenceError('pair receipt must cover exactly the two bound seals')

        for member in members:
            receipt = by_request[pair_request_id(pair_id, member.seal_id)]
            if (receipt.day, receipt.delta) != (member.day, member.delta):
                raise EvidenceError('pair leg does not match the bound request evidence')
            head = next_revision(connection, member.day, confirmation_frontier(connection, member.day))
            if head is None or head.seal_id != member.seal_id:
                raise EvidenceError('pair leg is not for the first unconfirmed seal')

        for member in members:
            receipt = by_request[pair_request_id(pair_id, member.seal_id)]
            connection.execute('''
                UPDATE paired_members
                SET receipt_id = ?, request_id = ?, posting_sequence = ?
                WHERE pair_id = ? AND seal_id = ?
            ''', (
                receipt.receipt_id, receipt.request_id, receipt.posting_sequence,
                pair_id, member.seal_id,
            ))
            connection.execute('''
                INSERT INTO confirmation_frontiers (day, sequence) VALUES (?, ?)
                ON CONFLICT (day) DO UPDATE SET sequence = excluded.sequence
            ''', (member.day, member.sequence))
        connection.execute(
            'UPDATE paired_reports SET status = ? WHERE pair_id = ?', (CONFIRMED, pair_id),
        )
        return load_pair(connection, pair_id)
