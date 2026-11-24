from contextlib import contextmanager


class InputError(ValueError):
    """An input does not satisfy the metering contract."""


class ConflictError(ValueError):
    """A business identity is already in use or its target is unavailable."""


class EvidenceError(RuntimeError):
    """An operation cannot establish the required ledger evidence."""


def require_name(value, field):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise InputError(f'{field} must be a nonblank text name without NUL')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise InputError(f'{field} must contain valid Unicode') from exc
    return value


def require_litres(value):
    if type(value) is not int:
        raise InputError('litres must be an integer')
    return value


def encode_litres(value):
    # Power-of-two conversion handles integers beyond the decimal digit limit.
    return format(value, 'x')


def decode_litres(value):
    return int(value, 16)


PAIR_REQUEST_PREFIX = 'pair:'


def pair_request_id(pair_id, seal_id):
    """Build the receiver-side request id used by one leg of a paired report."""
    return f'{PAIR_REQUEST_PREFIX}{pair_id}:{seal_id}'


@contextmanager
def transaction(connection, *, write=False):
    connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
    try:
        yield
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
