from close.revisions import next_revision
from dispatch.outbound import request_for_revision
from meter_core import EvidenceError, decode_litres, encode_litres, transaction
from receiver.receipts import Receipt


def init_confirm_schema(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS confirmations (
            seal_id TEXT PRIMARY KEY NOT NULL REFERENCES outbound_requests (seal_id),
            receipt_id TEXT NOT NULL UNIQUE,
            request_id TEXT NOT NULL UNIQUE,
            day TEXT NOT NULL,
            delta TEXT NOT NULL,
            posting_sequence INTEGER NOT NULL UNIQUE
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS confirmation_frontiers (
            day TEXT PRIMARY KEY NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence > 0),
            FOREIGN KEY (day, sequence) REFERENCES revisions (day, sequence)
        )
    ''')


def confirmation_frontier(connection, day):
    row = connection.execute(
        'SELECT sequence FROM confirmation_frontiers WHERE day = ?', (day,),
    ).fetchone()
    return 0 if row is None else row[0]


def confirmation_for_revision(connection, seal_id):
    row = connection.execute('''
        SELECT receipt_id, request_id, day, delta, posting_sequence
        FROM confirmations WHERE seal_id = ?
    ''', (seal_id,)).fetchone()
    if row is None:
        # Paired reports confirm both days outside the ordinary per-seal table.
        from dispatch.pairs import confirmed_receipt_for_seal
        return confirmed_receipt_for_seal(connection, seal_id)
    receipt_id, request_id, day, delta, sequence = row
    return Receipt(receipt_id, request_id, day, decode_litres(delta), sequence)


def confirm_receipt(connection, request, receipt):
    if (receipt.request_id, receipt.day, receipt.delta) != (
        request.request_id, request.day, request.delta,
    ):
        raise EvidenceError('receipt does not match the bound request')
    with transaction(connection, write=True):
        if request_for_revision(connection, request.seal_id) != request:
            raise EvidenceError('no matching durable request')
        existing = confirmation_for_revision(connection, request.seal_id)
        if existing is not None:
            if existing != receipt:
                raise EvidenceError('receipt differs from the original confirmation')
            return existing
        frontier = confirmation_frontier(connection, request.day)
        head = next_revision(connection, request.day, frontier)
        if head is None or (head.seal_id, head.sequence) != (request.seal_id, request.sequence):
            raise EvidenceError('receipt is not for the first unconfirmed seal')
        connection.execute('INSERT INTO confirmations VALUES (?, ?, ?, ?, ?, ?)', (
            request.seal_id, receipt.receipt_id, receipt.request_id, receipt.day,
            encode_litres(receipt.delta), receipt.posting_sequence,
        ))
        connection.execute('''
            INSERT INTO confirmation_frontiers (day, sequence) VALUES (?, ?)
            ON CONFLICT (day) DO UPDATE SET sequence = excluded.sequence
        ''', (request.day, request.sequence))
        return receipt
