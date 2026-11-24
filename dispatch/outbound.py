from dataclasses import dataclass

from meter_core import EvidenceError, decode_litres, encode_litres


@dataclass(frozen=True, slots=True)
class OutboundRequest:
    seal_id: str
    request_id: str
    day: str
    sequence: int
    delta: int


def init_outbound_schema(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS outbound_requests (
            seal_id TEXT PRIMARY KEY NOT NULL REFERENCES revisions (seal_id),
            request_id TEXT NOT NULL UNIQUE,
            day TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            delta TEXT NOT NULL,
            UNIQUE (day, sequence)
        )
    ''')


def request_for_revision(connection, seal_id):
    row = connection.execute('''
        SELECT seal_id, request_id, day, sequence, delta
        FROM outbound_requests WHERE seal_id = ?
    ''', (seal_id,)).fetchone()
    if row is None:
        return None
    return OutboundRequest(*row[:-1], decode_litres(row[-1]))


def bind_request(connection, revision, delta):
    request = OutboundRequest(
        revision.seal_id, f'seal:{revision.seal_id}', revision.day, revision.sequence, delta,
    )
    existing = request_for_revision(connection, revision.seal_id)
    if existing is not None:
        if existing != request:
            raise EvidenceError('a bound request cannot be changed')
        return existing
    connection.execute('INSERT INTO outbound_requests VALUES (?, ?, ?, ?, ?)', (
        request.seal_id, request.request_id, request.day, request.sequence,
        encode_litres(request.delta),
    ))
    return request
