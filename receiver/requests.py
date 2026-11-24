from close.calendar import parse_day
from meter_core import ConflictError, require_litres, require_name, transaction
from receiver.ledger import append_posting
from receiver.receipts import receipt_for_request, store_receipt


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
        return store_receipt(connection, append_posting(connection, request_id, day, delta))
