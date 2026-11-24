import sqlite3
from dataclasses import replace

from audit.lookup import lookup
from audit.reconcile import reconcile_posting
from audit.workspace import initialize_workspace, open_workspace
from close.finalize import seal
from dispatch.confirm import confirmation_frontier
from dispatch.pairs import pair_status
from dispatch.submit import ReplyLost, advance, advance_pair
from intake.batches import apply
from meter_core import ConflictError, EvidenceError, InputError
from receiver.balances import balance
from receiver.pairs import PairReceipt
from receiver.receipts import receipt_for_request
from receiver.requests import post
from tests.support import (
    DAY, LATER, NEXT, WorkspaceTestCase, block_commit, lose_reply,
)

PAIR_ID = 'march-transfer'
HEADS = ('march-01-second', 'march-02-second')
DAYS = (DAY, NEXT)


def build_pair_fixture(work):
    """Two base days reported at 5/12, then corrected and sealed at 2/15."""
    for index, (day, initial, corrected) in enumerate(zip(DAYS, (5, 12), (2, 15)), 1):
        apply(work.local, day, f'initial-{index}', 'initial',
              [('estuary', 'intake', initial)])
        seal(work.local, day, f'first-{index}')
        advance(work.local, work.external, day)
        apply(work.local, day, f'correction-{index}', 'correction',
              [('estuary', 'intake', corrected)])
        seal(work.local, day, HEADS[index - 1])


def lose_pair(_pair_receipt):
    raise ReplyLost('receiver committed the pair before the reply was lost')


def external_totals(work):
    return tuple(balance(work.external, day).total for day in DAYS)


def local_frontiers(work):
    return tuple(confirmation_frontier(work.local, day) for day in DAYS)


class PairedReportTests(WorkspaceTestCase):
    def test_pair_books_two_days_together_and_is_replayable(self):
        with self.open() as work:
            build_pair_fixture(work)
            result = advance_pair(work.local, work.external, PAIR_ID, HEADS)
            self.assertEqual(result.state, 'confirmed')
            self.assertEqual(result.pair_id, PAIR_ID)
            self.assertEqual([receipt.delta for receipt in result.receipts], [-3, 3])
            self.assertEqual([receipt.day for receipt in result.receipts], list(DAYS))
            self.assertTrue(all(
                receipt.request_id == f'pair:{PAIR_ID}:{seal_id}'
                for receipt, seal_id in zip(result.receipts, HEADS)
            ))
            self.assertEqual(external_totals(work), (2, 15))
            self.assertEqual(local_frontiers(work), (2, 2))
            for seal_id, delta in zip(HEADS, (-3, 3)):
                review = lookup(work.local, seal_id)
                self.assertEqual((review.state, review.delta), ('confirmed', delta))
                reconciliation = reconcile_posting(work.external, review)
                self.assertTrue(reconciliation.posting_matches)
                self.assertTrue(reconciliation.balance_matches)
                self.assertEqual(reconciliation.posting.delta, delta)
            # Replaying the same group (even reversed) reclaims the same receipts
            # and never adds postings.
            replayed = advance_pair(work.local, work.external, PAIR_ID, HEADS[::-1])
            self.assertEqual(replayed.state, 'confirmed')
            self.assertEqual(replayed.receipts, result.receipts)
            self.assertEqual(
                [len(balance(work.external, day).postings) for day in DAYS], [2, 2],
            )

    def test_lost_reply_reopens_and_reclaims_same_receipts(self):
        with self.open() as work:
            build_pair_fixture(work)
            lost = advance_pair(
                work.local, work.external, PAIR_ID, HEADS, after_post=lose_pair,
            )
            self.assertEqual((lost.state, lost.receipts), ('pending', ()))
            self.assertEqual(pair_status(work.local, PAIR_ID), 'pending')
            self.assertEqual([lookup(work.local, seal_id).state for seal_id in HEADS],
                             ['pending', 'pending'])
            self.assertEqual(external_totals(work), (2, 15))
            original = tuple(
                receipt_for_request(work.external, f'pair:{PAIR_ID}:{seal_id}')
                for seal_id in HEADS
            )
        with self.open() as work:
            recovered = advance_pair(work.local, work.external, PAIR_ID, HEADS[::-1])
            self.assertEqual(recovered.state, 'confirmed')
            self.assertEqual(recovered.receipts, original)
            self.assertEqual(external_totals(work), (2, 15))
            self.assertEqual(local_frontiers(work), (2, 2))
            # Reclaiming an already confirmed pair returns the stored result.
            again = advance_pair(work.local, work.external, PAIR_ID, HEADS)
            self.assertEqual(again.receipts, original)
            self.assertEqual(
                [len(balance(work.external, day).postings) for day in DAYS], [2, 2],
            )

    def test_ordinary_first_posting_wins_and_pair_cannot_sneak_other_leg_in(self):
        with self.open() as work:
            build_pair_fixture(work)
            single = advance(
                work.local, work.external, DAY, after_post=lose_reply,
            )
            self.assertEqual(single.state, 'pending')
            self.assertEqual(external_totals(work), (2, 12))
            with self.assertRaises(ConflictError):
                advance_pair(work.local, work.external, PAIR_ID, HEADS)
            self.assertEqual(pair_status(work.local, PAIR_ID), 'rejected')
            # The rejected pair left no receiver-side group or postings behind.
            self.assertEqual(external_totals(work), (2, 12))
            self.assertEqual(
                [len(balance(work.external, day).postings) for day in DAYS], [2, 1],
            )
        with self.open() as work:
            # Ordinary handling remains available on both days.
            first = advance(work.local, work.external, DAY)
            second = advance(work.local, work.external, NEXT)
            self.assertEqual((first.receipt.delta, second.receipt.delta), (-3, 3))
            self.assertEqual(external_totals(work), (2, 15))
            # Rebinding the original pair id to a different group is refused.
            apply(work.local, DAY, 'third-batch', 'correction',
                  [('estuary', 'intake', 4)])
            seal(work.local, DAY, 'third-1')
            apply(work.local, NEXT, 'third-batch-2', 'correction',
                  [('estuary', 'intake', 16)])
            seal(work.local, NEXT, 'third-2')
            with self.assertRaises(ConflictError):
                advance_pair(
                    work.local, work.external, PAIR_ID, ('third-1', 'third-2'),
                )

    def test_pair_first_posting_blocks_ordinary_member_confirmation(self):
        with self.open() as work:
            build_pair_fixture(work)
            advance_pair(
                work.local, work.external, PAIR_ID, HEADS, after_post=lose_pair,
            )
            # The ordinary entry must not add a posting or confirm either member.
            with self.assertRaises(ConflictError):
                advance(work.local, work.external, DAY)
            self.assertEqual([lookup(work.local, seal_id).state for seal_id in HEADS],
                             ['pending', 'pending'])
            self.assertEqual(
                [len(balance(work.external, day).postings) for day in DAYS], [2, 2],
            )
            # An unrelated day keeps reporting normally while the pair is pending.
            apply(work.local, LATER, 'other-batch', 'initial', [('x', 'y', 7)])
            seal(work.local, LATER, 'other-seal')
            other = advance(work.local, work.external, LATER)
            self.assertEqual((other.state, other.receipt.delta), ('confirmed', 7))
        with self.open() as work:
            recovered = advance_pair(work.local, work.external, PAIR_ID, HEADS[::-1])
            self.assertEqual(recovered.state, 'confirmed')
            self.assertEqual(external_totals(work), (2, 15))

    def test_at_most_one_unresolved_pair_per_day(self):
        with self.open() as work:
            build_pair_fixture(work)
            advance_pair(
                work.local, work.external, PAIR_ID, HEADS, after_post=lose_pair,
            )
            # Another pending pair touching either covered day is rejected.
            apply(work.local, '2025-03-10', 'zed', 'initial', [('x', 'y', 3)])
            seal(work.local, '2025-03-10', 'zed-seal')
            with self.assertRaises(ConflictError):
                advance_pair(
                    work.local, work.external, 'other-pair',
                    (HEADS[0], 'zed-seal'),
                )
            # Two entirely different days can still pair while the first is open.
            apply(work.local, '2025-03-11', 'alpha', 'initial', [('x', 'y', 10)])
            seal(work.local, '2025-03-11', 'alpha-first')
            advance(work.local, work.external, '2025-03-11')
            apply(work.local, '2025-03-11', 'alpha-fix', 'correction',
                  [('x', 'y', 7)])
            seal(work.local, '2025-03-11', 'alpha-seal')
            other = advance_pair(
                work.local, work.external, 'independent-pair',
                ('zed-seal', 'alpha-seal'),
            )
            self.assertEqual(other.state, 'confirmed')
            self.assertEqual(
                sorted(receipt.delta for receipt in other.receipts), [-3, 3],
            )

    def test_receiver_commit_failure_leaves_clean_state(self):
        with self.open() as work:
            build_pair_fixture(work)
            block_commit(work.external)
            try:
                with self.assertRaises(sqlite3.DatabaseError):
                    advance_pair(work.local, work.external, PAIR_ID, HEADS)
            finally:
                work.external.set_authorizer(None)
            self.assertEqual(external_totals(work), (5, 12))
            self.assertEqual(
                [len(balance(work.external, day).postings) for day in DAYS], [1, 1],
            )
            self.assertEqual(pair_status(work.local, PAIR_ID), 'pending')
        with self.open() as work:
            result = advance_pair(work.local, work.external, PAIR_ID, HEADS)
            self.assertEqual(result.state, 'confirmed')
            self.assertEqual(
                sorted(receipt.delta for receipt in result.receipts), [-3, 3],
            )
            self.assertEqual(external_totals(work), (2, 15))

    def test_local_confirmation_failure_keeps_receiver_result_and_reopens(self):
        with self.open() as work:
            build_pair_fixture(work)

            def fail_local_commit(_pair_receipt):
                block_commit(work.local)

            try:
                with self.assertRaises(sqlite3.DatabaseError):
                    advance_pair(
                        work.local, work.external, PAIR_ID, HEADS,
                        after_post=fail_local_commit,
                    )
            finally:
                work.local.set_authorizer(None)
            self.assertEqual(external_totals(work), (2, 15))
            self.assertEqual([lookup(work.local, seal_id).state for seal_id in HEADS],
                             ['pending', 'pending'])
            self.assertEqual(local_frontiers(work), (1, 1))
            original = tuple(
                receipt_for_request(work.external, f'pair:{PAIR_ID}:{seal_id}')
                for seal_id in HEADS
            )
        with self.open() as work:
            result = advance_pair(work.local, work.external, PAIR_ID, HEADS[::-1])
            self.assertEqual(result.state, 'confirmed')
            self.assertEqual(result.receipts, original)
            self.assertEqual(
                [len(balance(work.external, day).postings) for day in DAYS], [2, 2],
            )

    def test_invalid_pair_inputs(self):
        with self.open() as work:
            build_pair_fixture(work)
            for bad in (
                HEADS[0],
                [HEADS[0]],
                [HEADS[0], HEADS[0]],
                (HEADS[0], 'missing-seal'),
                42,
            ):
                with self.subTest(bad=bad), self.assertRaises((InputError, ConflictError)):
                    advance_pair(work.local, work.external, 'bad-pair', bad)
            apply(work.local, DAY, 'same-day-batch', 'correction',
                  [('estuary', 'intake', 9)])
            seal(work.local, DAY, 'same-day-seal')
            with self.assertRaises(InputError):
                advance_pair(
                    work.local, work.external, 'same-day-pair',
                    (HEADS[0], 'same-day-seal'),
                )
            with self.assertRaises(ConflictError):
                advance_pair(
                    work.local, work.external, 'old-head-pair',
                    ('first-1', HEADS[1]),
                )
            # A zero-delta head cannot take part in a pair.
            apply(work.local, '2025-04-01', 'zero-start', 'initial', [('x', 'y', 3)])
            seal(work.local, '2025-04-01', 'zero-first')
            advance(work.local, work.external, '2025-04-01')
            seal(work.local, '2025-04-01', 'zero-head')
            apply(work.local, '2025-04-02', 'zero-other', 'initial', [('x', 'y', 1)])
            seal(work.local, '2025-04-02', 'zero-other-head')
            with self.assertRaises(InputError):
                advance_pair(
                    work.local, work.external, 'zero-pair',
                    ('zero-head', 'zero-other-head'),
                )

    def test_later_revisions_report_normally_after_pair_confirmation(self):
        with self.open() as work:
            build_pair_fixture(work)
            advance_pair(work.local, work.external, PAIR_ID, HEADS)
            apply(work.local, DAY, 'post-pair-1', 'correction',
                  [('estuary', 'intake', 20)])
            seal(work.local, DAY, 'later-1')
            apply(work.local, NEXT, 'post-pair-2', 'correction',
                  [('estuary', 'intake', 30)])
            seal(work.local, NEXT, 'later-2')
            first = advance(work.local, work.external, DAY)
            second = advance(work.local, work.external, NEXT)
            self.assertEqual((first.receipt.delta, second.receipt.delta), (18, 15))
            self.assertEqual(external_totals(work), (20, 30))
            self.assertEqual(advance(work.local, work.external, DAY).state, 'idle')

    def test_tampered_pair_evidence_does_not_move_frontiers(self):
        with self.open() as work:
            build_pair_fixture(work)
            advance_pair(
                work.local, work.external, PAIR_ID, HEADS, after_post=lose_pair,
            )
            genuine = tuple(
                receipt_for_request(work.external, f'pair:{PAIR_ID}:{seal_id}')
                for seal_id in HEADS
            )
            bundle = PairReceipt(PAIR_ID, genuine)
            for tampered in (
                replace(bundle, pair_id='intruder'),
                PairReceipt(PAIR_ID, (genuine[0],)),
                PairReceipt(PAIR_ID, (replace(genuine[0], delta=99), genuine[1])),
                PairReceipt(
                    PAIR_ID,
                    (replace(genuine[0], request_id='pair:march-transfer:stranger'),
                     genuine[1]),
                ),
            ):
                with self.subTest(tampered=tampered), self.assertRaises(
                    (EvidenceError, ConflictError),
                ):
                    from dispatch.pairs import confirm_pair
                    confirm_pair(work.local, PAIR_ID, tampered)
            self.assertEqual(local_frontiers(work), (1, 1))
            self.assertEqual([lookup(work.local, seal_id).state for seal_id in HEADS],
                             ['pending', 'pending'])

    def test_v1_workspace_gains_pair_schema_on_reinitialize(self):
        root = self.root / 'v1-legacy'
        initialize_workspace(root)
        with open_workspace(root) as work:
            build_pair_fixture(work)
        with sqlite3.connect(root / 'warehouse.sqlite3') as connection:
            connection.execute('DROP TABLE paired_members')
            connection.execute('DROP TABLE paired_reports')
        with sqlite3.connect(root / 'receiver.sqlite3') as connection:
            connection.execute('DROP TABLE pair_groups')
            connection.execute('DROP TABLE seal_claims')
        initialize_workspace(root)
        initialize_workspace(root)
        with open_workspace(root) as work:
            result = advance_pair(work.local, work.external, PAIR_ID, HEADS)
            self.assertEqual(result.state, 'confirmed')
            self.assertEqual(external_totals(work), (2, 15))
            self.assertEqual(lookup(work.local, HEADS[0]).receipt, result.receipts[0])

    def test_receiver_rejects_direct_conflicting_claims(self):
        with self.open() as work:
            build_pair_fixture(work)
            result = advance_pair(work.local, work.external, PAIR_ID, HEADS)
            # A direct single post for the same seal request family is refused.
            with self.assertRaises(ConflictError):
                post(work.external, f'seal:{HEADS[0]}', DAY, -3)
            # The original pair receipts remain the only postings for those seals.
            for seal_id, receipt in zip(HEADS, result.receipts):
                self.assertEqual(
                    receipt_for_request(
                        work.external, f'pair:{PAIR_ID}:{seal_id}',
                    ),
                    receipt,
                )
