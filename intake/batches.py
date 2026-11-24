from dataclasses import dataclass

from intake.corrections import apply_corrections
from intake.identity import batch_fingerprint
from intake.observations import insert_observation
from intake.source_detail import store_source_detail
from intake.validate import validate_batch
from meter_core import ConflictError, transaction


@dataclass(frozen=True, slots=True)
class BatchResult:
    batch_id: str
    day: str
    sequence: int
    changed: int


def init_batch_schema(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS batches (
            batch_id TEXT PRIMARY KEY NOT NULL,
            day TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence > 0),
            kind TEXT NOT NULL CHECK (kind IN ('initial', 'correction')),
            fingerprint TEXT NOT NULL,
            changed INTEGER NOT NULL CHECK (changed >= 0),
            UNIQUE (day, sequence)
        )
    ''')


def latest_batch_sequence(connection, day):
    (sequence,) = connection.execute(
        'SELECT COALESCE(MAX(sequence), 0) FROM batches WHERE day = ?', (day,),
    ).fetchone()
    return sequence


def apply(connection, day, batch_id, kind, rows):
    batch = validate_batch(day, batch_id, kind, rows)
    fingerprint = batch_fingerprint(batch)
    with transaction(connection, write=True):
        stored = connection.execute(
            'SELECT day, sequence, changed, fingerprint FROM batches WHERE batch_id = ?',
            (batch.batch_id,),
        ).fetchone()
        if stored is not None:
            stored_day, sequence, changed, stored_fingerprint = stored
            if stored_fingerprint != fingerprint:
                raise ConflictError(f'batch identity has different parameters: {batch.batch_id!r}')
            return BatchResult(batch.batch_id, stored_day, sequence, changed)

        sequence = latest_batch_sequence(connection, batch.day) + 1
        if batch.kind == 'initial':
            for station, name, litres in batch.rows:
                insert_observation(connection, batch.day, station, name, litres)
            changed = 0
        else:
            changed = apply_corrections(connection, batch)
        connection.execute('INSERT INTO batches VALUES (?, ?, ?, ?, ?, ?)', (
            batch.batch_id, batch.day, sequence, batch.kind, fingerprint, changed,
        ))
        store_source_detail(connection, batch)
        return BatchResult(batch.batch_id, batch.day, sequence, changed)
