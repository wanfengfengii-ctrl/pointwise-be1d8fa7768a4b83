import sqlite3
from dataclasses import dataclass
from pathlib import Path

from audit.purge import init_purge_schema
from close.revisions import init_revision_schema
from dispatch.confirm import init_confirm_schema
from dispatch.outbound import init_outbound_schema
from intake.batches import init_batch_schema
from intake.observations import init_observation_schema
from intake.source_detail import init_source_schema
from meter_core import InputError, transaction
from receiver.ledger import init_ledger_schema
from receiver.receipts import init_receipt_schema


@dataclass(slots=True)
class Workspace:
    local: sqlite3.Connection
    external: sqlite3.Connection

    def close(self):
        try:
            self.local.close()
        finally:
            self.external.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


def _connect(path, mode):
    connection = sqlite3.connect(
        f'{path.as_uri()}?mode={mode}', uri=True, isolation_level=None, timeout=5.0,
    )
    try:
        connection.execute('PRAGMA foreign_keys = ON')
        return connection
    except BaseException:
        connection.close()
        raise


def _open(root, mode):
    root = Path(root).resolve()
    local_path = root / 'warehouse.sqlite3'
    external_path = root / 'receiver.sqlite3'
    if local_path.resolve() == external_path.resolve() or (
        local_path.exists() and external_path.exists() and local_path.samefile(external_path)
    ):
        raise InputError('warehouse and receiver must be separate database files')
    local = _connect(local_path, mode)
    try:
        return Workspace(local, _connect(external_path, mode))
    except BaseException:
        local.close()
        raise


def initialize_workspace(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with _open(root, 'rwc') as workspace:
        with transaction(workspace.local, write=True):
            for initialize in (
                init_batch_schema, init_observation_schema, init_source_schema,
                init_revision_schema, init_outbound_schema, init_confirm_schema, init_purge_schema,
            ):
                initialize(workspace.local)
        with transaction(workspace.external, write=True):
            init_ledger_schema(workspace.external)
            init_receipt_schema(workspace.external)


def open_workspace(root):
    return _open(root, 'rw')
