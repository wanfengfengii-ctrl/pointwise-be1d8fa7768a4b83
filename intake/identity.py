import hashlib
import json

from meter_core import encode_litres


def batch_fingerprint(batch):
    rows = [(station, name, encode_litres(value)) for station, name, value in batch.rows]
    canonical = json.dumps(
        [batch.day, batch.batch_id, batch.kind, rows], ensure_ascii=True,
        separators=(',', ':'),
    )
    return hashlib.sha256(canonical.encode('ascii')).hexdigest()
