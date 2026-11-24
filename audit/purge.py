from dataclasses import dataclass

from audit.retention import CoveredSource, eligible_source_batches
from close.calendar import parse_day
from intake.source_detail import delete_source_detail
from meter_core import transaction


@dataclass(frozen=True, slots=True)
class PurgeResult:
    cutoff: str
    removed: tuple[CoveredSource, ...]


@dataclass(frozen=True, slots=True)
class PurgeEntry:
    batch_id: str
    covering_seal_id: str
    cutoff: str


def init_purge_schema(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS purge_entries (
            batch_id TEXT PRIMARY KEY NOT NULL REFERENCES batches (batch_id),
            covering_seal_id TEXT NOT NULL REFERENCES revisions (seal_id),
            cutoff TEXT NOT NULL
        )
    ''')


def purge(connection, cutoff):
    cutoff = parse_day(cutoff)
    with transaction(connection, write=True):
        eligible = eligible_source_batches(connection, cutoff)
        for source in eligible:
            delete_source_detail(connection, source.batch_id)
            connection.execute('INSERT INTO purge_entries VALUES (?, ?, ?)', (
                source.batch_id, source.covering_seal_id, cutoff,
            ))
        return PurgeResult(cutoff, eligible)


def purge_history(connection):
    rows = connection.execute('''
        SELECT p.batch_id, p.covering_seal_id, p.cutoff
        FROM purge_entries AS p JOIN batches AS b ON b.batch_id = p.batch_id
        ORDER BY b.day, b.sequence
    ''')
    return tuple(PurgeEntry(*row) for row in rows)
