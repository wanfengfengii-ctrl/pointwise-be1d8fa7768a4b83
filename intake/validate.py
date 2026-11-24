from dataclasses import dataclass

from close.calendar import parse_day
from meter_core import InputError, require_litres, require_name


@dataclass(frozen=True, slots=True)
class Batch:
    day: str
    batch_id: str
    kind: str
    rows: tuple[tuple[str, str, int], ...]


def validate_batch(day, batch_id, kind, rows):
    day = parse_day(day)
    batch_id = require_name(batch_id, 'batch_id')
    if kind not in ('initial', 'correction'):
        raise InputError('kind must be initial or correction')
    if not isinstance(rows, (list, tuple)):
        raise InputError('rows must be a list or tuple of (station, name, litres)')
    validated = []
    identities = set()
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            raise InputError('every row must contain station, name and litres')
        station = require_name(row[0], 'station')
        name = require_name(row[1], 'row name')
        litres = require_litres(row[2])
        identity = (station, name)
        if identity in identities:
            raise InputError(f'duplicate station row in batch: {identity!r}')
        identities.add(identity)
        validated.append((station, name, litres))
    return Batch(day, batch_id, kind, tuple(validated))
