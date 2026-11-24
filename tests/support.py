import sqlite3
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from audit.workspace import initialize_workspace, open_workspace
from close.finalize import seal
from dispatch.submit import ReplyLost
from intake.batches import apply

DAY = '2025-03-01'
NEXT = '2025-03-02'
LATER = '2025-03-03'


class WorkspaceTestCase(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix='estuary-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        initialize_workspace(self.root)

    def open(self):
        return open_workspace(self.root)


def seed(connection, seal_id='v1'):
    apply(connection, DAY, 'initial', 'initial', [('east', 'a', 2), ('east', 'b', 3)])
    return seal(connection, DAY, seal_id)


def correct_and_seal(connection, batch_id, value, seal_id):
    apply(connection, DAY, batch_id, 'correction', [('east', 'b', value)])
    return seal(connection, DAY, seal_id)


def lose_reply(receipt):
    raise ReplyLost(f'reply lost for {receipt.request_id}')


def block_commit(connection):
    def authorize(action, parameter, *_):
        if action == sqlite3.SQLITE_TRANSACTION and parameter == 'COMMIT':
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)


@contextmanager
def rejected_commit(connection):
    block_commit(connection)
    try:
        yield
    finally:
        connection.set_authorizer(None)
