from meter_core import ConflictError, decode_litres, encode_litres


def init_observation_schema(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS observations (
            day TEXT NOT NULL,
            station TEXT NOT NULL,
            name TEXT NOT NULL,
            litres TEXT NOT NULL,
            PRIMARY KEY (day, station, name)
        ) WITHOUT ROWID
    ''')


def insert_observation(connection, day, station, name, litres):
    if current_amount(connection, day, station, name) is not None:
        raise ConflictError(f'observation already exists: {(day, station, name)!r}')
    connection.execute(
        'INSERT INTO observations (day, station, name, litres) VALUES (?, ?, ?, ?)',
        (day, station, name, encode_litres(litres)),
    )


def current_amount(connection, day, station, name):
    row = connection.execute(
        'SELECT litres FROM observations WHERE day = ? AND station = ? AND name = ?',
        (day, station, name),
    ).fetchone()
    return None if row is None else decode_litres(row[0])


def replace_current_amount(connection, day, station, name, litres):
    updated = connection.execute(
        'UPDATE observations SET litres = ? WHERE day = ? AND station = ? AND name = ?',
        (encode_litres(litres), day, station, name),
    )
    if updated.rowcount != 1:
        raise ConflictError(f'unknown observation: {(day, station, name)!r}')


def iter_effective_amounts(connection, day):
    rows = connection.execute('SELECT litres FROM observations WHERE day = ?', (day,))
    for (litres,) in rows:
        yield decode_litres(litres)
