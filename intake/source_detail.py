from dataclasses import dataclass

from meter_core import encode_litres


@dataclass(frozen=True, slots=True)
class SourceKey:
    batch_id: str
    day: str
    sequence: int


def init_source_schema(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS source_batches (
            batch_id TEXT PRIMARY KEY NOT NULL REFERENCES batches (batch_id)
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS source_rows (
            batch_id TEXT NOT NULL REFERENCES source_batches (batch_id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            station TEXT NOT NULL,
            name TEXT NOT NULL,
            litres TEXT NOT NULL,
            PRIMARY KEY (batch_id, position)
        ) WITHOUT ROWID
    ''')


def store_source_detail(connection, batch):
    connection.execute('INSERT INTO source_batches VALUES (?)', (batch.batch_id,))
    connection.executemany(
        'INSERT INTO source_rows VALUES (?, ?, ?, ?, ?)',
        (
            (batch.batch_id, position, station, name, encode_litres(litres))
            for position, (station, name, litres) in enumerate(batch.rows, start=1)
        ),
    )


def list_source_keys(connection, cutoff):
    rows = connection.execute('''
        SELECT b.batch_id, b.day, b.sequence
        FROM source_batches AS s JOIN batches AS b ON b.batch_id = s.batch_id
        WHERE b.day < ? ORDER BY b.day, b.sequence
    ''', (cutoff,))
    return tuple(SourceKey(*row) for row in rows)


def delete_source_detail(connection, batch_id):
    connection.execute('DELETE FROM source_batches WHERE batch_id = ?', (batch_id,))
