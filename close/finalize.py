from close.calendar import parse_day
from close.revisions import append_revision
from close.totals import total_for_day
from intake.batches import latest_batch_sequence
from meter_core import require_name, transaction


def seal(connection, day, seal_id):
    day = parse_day(day)
    seal_id = require_name(seal_id, 'seal_id')
    with transaction(connection, write=True):
        total = total_for_day(connection, day)
        source_watermark = latest_batch_sequence(connection, day)
        return append_revision(connection, day, seal_id, total, source_watermark)
