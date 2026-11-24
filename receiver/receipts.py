from dataclasses import dataclass

from meter_core import decode_litres, encode_litres


@dataclass(frozen=True, slots=True)
class Receipt:
    receipt_id: str
    request_id: str
    day: str
    delta: int
    posting_sequence: int


def init_receipt_schema(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS receipts (
            request_id TEXT PRIMARY KEY NOT NULL REFERENCES postings (request_id),
            receipt_id TEXT NOT NULL UNIQUE,
            day TEXT NOT NULL,
            delta TEXT NOT NULL,
            posting_sequence INTEGER NOT NULL UNIQUE REFERENCES postings (sequence)
        )
    ''')


def receipt_for_request(connection, request_id):
    row = connection.execute('''
        SELECT receipt_id, request_id, day, delta, posting_sequence
        FROM receipts WHERE request_id = ?
    ''', (request_id,)).fetchone()
    if row is None:
        return None
    receipt_id, request_id, day, delta, sequence = row
    return Receipt(receipt_id, request_id, day, decode_litres(delta), sequence)


def store_receipt(connection, posting):
    receipt = Receipt(
        f'receipt-{posting.sequence}', posting.request_id, posting.day,
        posting.delta, posting.sequence,
    )
    connection.execute('''
        INSERT INTO receipts (request_id, receipt_id, day, delta, posting_sequence)
        VALUES (?, ?, ?, ?, ?)
    ''', (
        receipt.request_id, receipt.receipt_id, receipt.day, encode_litres(receipt.delta),
        receipt.posting_sequence,
    ))
    return receipt
