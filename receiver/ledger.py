from dataclasses import dataclass

from meter_core import decode_litres, encode_litres


@dataclass(frozen=True, slots=True)
class Posting:
    sequence: int
    request_id: str
    day: str
    delta: int


def init_ledger_schema(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS postings (
            sequence INTEGER PRIMARY KEY,
            request_id TEXT NOT NULL UNIQUE,
            day TEXT NOT NULL,
            delta TEXT NOT NULL
        )
    ''')
    connection.execute('CREATE INDEX IF NOT EXISTS postings_by_day ON postings (day, sequence)')


def append_posting(connection, request_id, day, delta):
    inserted = connection.execute(
        'INSERT INTO postings (request_id, day, delta) VALUES (?, ?, ?)',
        (request_id, day, encode_litres(delta)),
    )
    return Posting(inserted.lastrowid, request_id, day, delta)


def read_day_postings(connection, day):
    rows = connection.execute('''
        SELECT sequence, request_id, day, delta FROM postings
        WHERE day = ? ORDER BY sequence
    ''', (day,)).fetchall()
    return tuple(Posting(sequence, request, day, decode_litres(delta))
                 for sequence, request, day, delta in rows)
