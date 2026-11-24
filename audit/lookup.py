from audit.reconcile import read_review_evidence
from close.revisions import get_revision
from meter_core import require_name, transaction


def lookup(connection, seal_id):
    seal_id = require_name(seal_id, 'seal_id')
    with transaction(connection):
        revision = get_revision(connection, seal_id)
        return None if revision is None else read_review_evidence(connection, revision)
