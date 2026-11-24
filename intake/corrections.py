from intake.observations import current_amount, replace_current_amount
from meter_core import ConflictError


def apply_corrections(connection, batch):
    changed = 0
    for station, name, litres in batch.rows:
        old = current_amount(connection, batch.day, station, name)
        if old is None:
            raise ConflictError(f'no observation to correct: {(batch.day, station, name)!r}')
        if old != litres:
            replace_current_amount(connection, batch.day, station, name, litres)
            changed += 1
    return changed
