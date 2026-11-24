from close.calendar import parse_day
from meter_core import ConflictError, require_litres, require_name, transaction
from receiver.claims import ensure_single_claim
from receiver.ledger import append_posting
from receiver.receipts import receipt_for_request, store_receipt

SEAL_REQUEST_PREFIX = 'seal:'


def post(connection, request_id, day, delta):
    request_id = require_name(request_id, 'request_id')
    day = parse_day(day)
    delta = require_litres(delta)
    with transaction(connection, write=True):
        receipt = receipt_for_request(connection, request_id)
        if receipt is not None:
            if (receipt.day, receipt.delta) != (day, delta):
                raise ConflictError(f'request identity has different parameters: {request_id!r}')
            return receipt
        posting = append_posting(connection, request_id, day, delta)
        stored = store_receipt(connection, posting)
        if request_id.startswith(SEAL_REQUEST_PREFIX):
            # Ordinary metering reports compete with paired reports for the
            # first posting of each sealed revision.
            ensure_single_claim(connection, request_id[len(SEAL_REQUEST_PREFIX):], request_id)
        return stored
