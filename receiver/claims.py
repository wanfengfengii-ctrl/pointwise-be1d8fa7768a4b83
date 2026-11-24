from meter_core import ConflictError, require_name


def init_claim_schema(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS seal_claims (
            seal_id TEXT PRIMARY KEY NOT NULL,
            request_id TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL CHECK (kind IN ('single', 'pair'))
        )
    ''')


def claim_for_seal(connection, seal_id):
    row = connection.execute(
        'SELECT seal_id, request_id, kind FROM seal_claims WHERE seal_id = ?',
        (seal_id,),
    ).fetchone()
    return None if row is None else tuple(row)


def ensure_single_claim(connection, seal_id, request_id):
    """Record the first-posting ownership of a seal for an ordinary report.

    Replaying the same winning request is idempotent; any claim that belongs
    to another request rejects the ordinary entry.
    """
    seal_id = require_name(seal_id, 'seal_id')
    existing = claim_for_seal(connection, seal_id)
    if existing is None:
        connection.execute(
            'INSERT INTO seal_claims (seal_id, request_id, kind) VALUES (?, ?, ?)',
            (seal_id, request_id, 'single'),
        )
        return
    if existing[1] != request_id:
        raise ConflictError(f'seal already claimed by another report: {seal_id!r}')
