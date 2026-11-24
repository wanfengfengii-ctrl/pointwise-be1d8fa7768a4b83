from dataclasses import dataclass

from close.revisions import Revision
from dispatch.confirm import confirmation_for_revision
from receiver.balances import balance
from receiver.ledger import Posting
from receiver.receipts import Receipt


@dataclass(frozen=True, slots=True)
class Review:
    revision: Revision
    receipt: Receipt | None

    @property
    def state(self):
        return 'pending' if self.receipt is None else 'confirmed'

    @property
    def delta(self):
        return None if self.receipt is None else self.receipt.delta


@dataclass(frozen=True, slots=True)
class Reconciliation:
    review: Review
    posting: Posting | None
    posting_matches: bool | None
    day_balance: int | None
    balance_matches: bool | None


def read_review_evidence(connection, revision):
    return Review(revision, confirmation_for_revision(connection, revision.seal_id))


def reconcile_posting(external, review):
    receipt = review.receipt
    if receipt is None:
        return Reconciliation(review, None, None, None, None)

    account = balance(external, review.revision.day)
    posting = next((entry for entry in account.postings if entry.request_id == receipt.request_id), None)
    posting_matches = posting == Posting(
        receipt.posting_sequence, receipt.request_id, receipt.day, receipt.delta,
    )
    balance_matches = None
    if posting_matches and posting == account.postings[-1]:
        balance_matches = account.total == review.revision.total
    return Reconciliation(review, posting, posting_matches, account.total, balance_matches)
