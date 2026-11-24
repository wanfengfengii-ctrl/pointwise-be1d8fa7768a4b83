from dataclasses import dataclass

from meter_core import ConflictError, decode_litres, encode_litres


@dataclass(frozen=True, slots=True)
class Revision:
    seal_id: str
    day: str
    sequence: int
    total: int
    source_watermark: int


_COLUMNS = 'seal_id, day, sequence, total, source_watermark'


def init_revision_schema(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS revisions (
            seal_id TEXT PRIMARY KEY NOT NULL,
            day TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence > 0),
            total TEXT NOT NULL,
            source_watermark INTEGER NOT NULL CHECK (source_watermark >= 0),
            UNIQUE (day, sequence)
        )
    ''')


def _revision(row):
    if row is None:
        return None
    seal_id, day, sequence, total, source_watermark = row
    return Revision(seal_id, day, sequence, decode_litres(total), source_watermark)


def get_revision(connection, seal_id):
    row = connection.execute(
        f'SELECT {_COLUMNS} FROM revisions WHERE seal_id = ?', (seal_id,),
    ).fetchone()
    return _revision(row)


def latest_revision(connection, day):
    row = connection.execute(
        f'SELECT {_COLUMNS} FROM revisions WHERE day = ? ORDER BY sequence DESC LIMIT 1',
        (day,),
    ).fetchone()
    return _revision(row)


def get_revision_at(connection, day, sequence):
    row = connection.execute(
        f'SELECT {_COLUMNS} FROM revisions WHERE day = ? AND sequence = ?',
        (day, sequence),
    ).fetchone()
    return _revision(row)


def next_revision(connection, day, after_sequence):
    return get_revision_at(connection, day, after_sequence + 1)


def append_revision(connection, day, seal_id, total, source_watermark):
    if get_revision(connection, seal_id) is not None:
        raise ConflictError(f'seal identity already used: {seal_id!r}')
    previous = latest_revision(connection, day)
    sequence = 1 if previous is None else previous.sequence + 1
    connection.execute('INSERT INTO revisions VALUES (?, ?, ?, ?, ?)', (
        seal_id, day, sequence, encode_litres(total), source_watermark,
    ))
    return Revision(seal_id, day, sequence, total, source_watermark)
