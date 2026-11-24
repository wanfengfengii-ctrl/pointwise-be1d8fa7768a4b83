import sqlite3

from audit.lookup import lookup
from audit.purge import purge, purge_history
from audit.reconcile import reconcile_posting
from audit.workspace import initialize_workspace
from close.finalize import seal
from dispatch.outbound import request_for_revision
from dispatch.submit import advance
from intake.batches import apply, latest_batch_sequence
from intake.source_detail import list_source_keys
from meter_core import ConflictError, InputError
from receiver.balances import balance
from receiver.receipts import receipt_for_request
from tests.support import DAY, LATER, NEXT, WorkspaceTestCase, correct_and_seal, lose_reply, rejected_commit, seed


def source_ids(connection):
    return [key.batch_id for key in list_source_keys(connection, '2025-04-01')]


class AuditTests(WorkspaceTestCase):
    def test_purge_unsent_revision(self):
        with self.open() as work:
            first = seed(work.local)
            second = correct_and_seal(work.local, 'second', 10, 'v2')
            self.assertEqual(balance(work.external, DAY).postings, ())
            self.assertIsNone(request_for_revision(work.local, first.seal_id))
            self.assertIsNone(request_for_revision(work.local, second.seal_id))
            removed = purge(work.local, NEXT).removed
            self.assertEqual([item.batch_id for item in removed], ['initial', 'second'])
            self.assertEqual(source_ids(work.local), [])
            self.assertEqual(work.local.execute('SELECT COUNT(*) FROM source_rows').fetchone(), (0,))
        with self.open() as work:
            for revision, delta in ((first, 5), (second, 7)):
                self.assertEqual(lookup(work.local, revision.seal_id).state, 'pending')
                result = advance(work.local, work.external, DAY)
                self.assertEqual((result.seal_id, result.receipt.delta), (revision.seal_id, delta))
                review = lookup(work.local, revision.seal_id)
                self.assertEqual((review.revision, review.receipt), (revision, result.receipt))
                self.assertTrue(reconcile_posting(work.external, review).posting_matches)
            self.assertEqual(balance(work.external, DAY).total, 12)
            self.assertEqual(advance(work.local, work.external, DAY).state, 'idle')

    def test_backlog_after_confirmed_frontier(self):
        with self.open() as work:
            seed(work.local)
            first = advance(work.local, work.external, DAY).receipt
            correct_and_seal(work.local, 'second', 10, 'v2')
            correct_and_seal(work.local, 'third', 7, 'v3')
            self.assertIsNone(request_for_revision(work.local, 'v2'))
            self.assertEqual(len(purge(work.local, NEXT).removed), 3)
        with self.open() as work:
            self.assertEqual(source_ids(work.local), [])
            results = [advance(work.local, work.external, DAY) for _ in range(2)]
            actual = [(result.seal_id, result.receipt.delta if result.receipt else None) for result in results]
            expected = [('v2', 7), ('v3', -3)]
            self.assertEqual(actual, expected, f'DAILY_DELTA_MISMATCH actual={actual!r} expected={expected!r}')
            self.assertEqual(advance(work.local, work.external, DAY).state, 'idle')
            self.assertEqual(lookup(work.local, 'v1').receipt, first)
            self.assertEqual([lookup(work.local, seal_id).delta for seal_id in ('v2', 'v3')], [7, -3])
            account = balance(work.external, DAY)
            self.assertEqual(account.total, 9)
            self.assertEqual([entry.delta for entry in account.postings], [5, 7, -3])

    def test_covered_batches_only(self):
        with self.open() as work:
            seed(work.local)
            correction = [('east', 'b', 10)]
            accepted = apply(work.local, DAY, 'unsealed', 'correction', correction)
            apply(work.local, '2025-02-28', 'no-seal', 'initial', [('west', 'b', 3)])
            apply(work.local, NEXT, 'on-cutoff', 'initial', [('east', 'a', 9)])
            apply(work.local, LATER, 'after-cutoff', 'initial', [('east', 'a', 11)])
            seal(work.local, NEXT, 'cutoff-close')
            seal(work.local, LATER, 'later-close')
            with self.assertRaises(InputError):
                purge(work.local, '2025-03-42')
            self.assertEqual(purge_history(work.local), ())
            removed = purge(work.local, NEXT).removed
            self.assertEqual([(item.batch_id, item.covering_seal_id) for item in removed], [('initial', 'v1')])
            self.assertEqual(source_ids(work.local), ['no-seal', 'unsealed', 'on-cutoff', 'after-cutoff'])
            self.assertEqual(seal(work.local, DAY, 'v2').total, 12)
            removed = purge(work.local, NEXT).removed
            self.assertEqual([(item.batch_id, item.covering_seal_id) for item in removed], [('unsealed', 'v2')])
            self.assertEqual(source_ids(work.local), ['no-seal', 'on-cutoff', 'after-cutoff'])
            self.assertEqual(apply(work.local, DAY, 'unsealed', 'correction', correction), accepted)
            with self.assertRaises(ConflictError):
                apply(work.local, DAY, 'unsealed', 'correction', [('east', 'b', 11)])
            self.assertEqual(latest_batch_sequence(work.local, DAY), 2)
            self.assertEqual(purge(work.local, NEXT).removed, ())
            self.assertEqual([(entry.batch_id, entry.covering_seal_id, entry.cutoff)
                              for entry in purge_history(work.local)], [('initial', 'v1', NEXT), ('unsealed', 'v2', NEXT)])

    def test_purge_rollback(self):
        with self.open() as work:
            seed(work.local)
            original_rows = work.local.execute('SELECT * FROM source_rows ORDER BY batch_id, position').fetchall()
            self.assertEqual(len(original_rows), 2)
            with rejected_commit(work.local), self.assertRaises(sqlite3.DatabaseError):
                purge(work.local, NEXT)
        with self.open() as work:
            self.assertEqual(source_ids(work.local), ['initial'])
            self.assertEqual(work.local.execute('SELECT * FROM source_rows ORDER BY batch_id, position').fetchall(), original_rows)
            self.assertEqual(purge_history(work.local), ())
            self.assertEqual([item.batch_id for item in purge(work.local, NEXT).removed], ['initial'])
            self.assertEqual(work.local.execute('SELECT COUNT(*) FROM source_rows').fetchone(), (0,))

    def test_reopen_and_day_isolation(self):
        with self.open() as work:
            first = seed(work.local)
            apply(work.local, NEXT, 'next', 'initial', [('east', 'a', 4)])
            other = seal(work.local, NEXT, 'other')
            next_receipt = advance(work.local, work.external, NEXT).receipt
            first_receipt = advance(work.local, work.external, DAY).receipt
            self.assertEqual((first_receipt.delta, next_receipt.delta), (5, 4))
        initialize_workspace(self.root)
        with self.open() as work:
            for revision, receipt in ((first, first_receipt), (other, next_receipt)):
                review = lookup(work.local, revision.seal_id)
                self.assertEqual((review.revision, review.receipt), (revision, receipt))
                account = balance(work.external, revision.day)
                self.assertEqual((account.total, len(account.postings)), (revision.total, 1))
                self.assertTrue(reconcile_posting(work.external, review).balance_matches)
            self.assertEqual(advance(work.local, work.external, LATER).state, 'idle')
            self.assertIsNone(lookup(work.local, 'unknown'))

    def test_historical_lookup(self):
        with self.open() as work:
            first = seed(work.local)
            original = advance(work.local, work.external, DAY).receipt
            second = correct_and_seal(work.local, 'second', 10, 'v2')
            advance(work.local, work.external, DAY, after_post=lose_reply)
            pending = reconcile_posting(work.external, lookup(work.local, second.seal_id))
            self.assertIsNone(pending.posting_matches)
            self.assertIsNone(pending.balance_matches)
            following = advance(work.local, work.external, DAY).receipt
            purge(work.local, NEXT)
        with self.open() as work:
            old = lookup(work.local, first.seal_id)
            new = lookup(work.local, second.seal_id)
            self.assertEqual((old.revision, old.receipt, old.delta), (first, original, 5))
            self.assertEqual((new.revision, new.receipt, new.delta), (second, following, 7))
            old_check = reconcile_posting(work.external, old)
            new_check = reconcile_posting(work.external, new)
            self.assertTrue(old_check.posting_matches)
            self.assertEqual(old_check.posting.delta, 5)
            self.assertEqual(old_check.day_balance, 12)
            self.assertIsNone(old_check.balance_matches)
            self.assertTrue(new_check.posting_matches)
            self.assertTrue(new_check.balance_matches)

    def test_reinitialize_preserves_user_data(self):
        with self.open() as work:
            initial = apply(work.local, DAY, 'user-batch', 'initial', [('user-site', 'a', 17)])
            apply(work.local, DAY, 'user-correction', 'correction', [('user-site', 'a', 21)])
            user = seal(work.local, DAY, 'user-seal')
            user_receipt = advance(work.local, work.external, DAY).receipt
            apply(work.local, NEXT, 'user-other', 'initial', [('user-site', 'a', 6)])
            other = seal(work.local, NEXT, 'user-other-seal')
            advance(work.local, work.external, NEXT, after_post=lose_reply)
            original_binding = request_for_revision(work.local, other.seal_id)
            remote_receipt = receipt_for_request(work.external, original_binding.request_id)
            original_accounts = tuple(balance(work.external, day) for day in (DAY, NEXT))
        initialize_workspace(self.root)
        initialize_workspace(self.root)
        with self.open() as work:
            self.assertEqual(original_accounts, tuple(balance(work.external, day) for day in (DAY, NEXT)))
            self.assertEqual([item.total for item in original_accounts], [21, 6])
            self.assertEqual(lookup(work.local, user.seal_id).receipt, user_receipt)
            self.assertEqual(lookup(work.local, other.seal_id).revision, other)
            self.assertEqual(lookup(work.local, other.seal_id).state, 'pending')
            self.assertEqual(request_for_revision(work.local, other.seal_id), original_binding)
            self.assertEqual(advance(work.local, work.external, NEXT).receipt, remote_receipt)
            self.assertEqual(receipt_for_request(work.external, user_receipt.request_id), user_receipt)
            self.assertEqual(apply(work.local, DAY, 'user-batch', 'initial', [('user-site', 'a', 17)]), initial)
            self.assertEqual(seal(work.local, DAY, 'user-after-init').total, 21)
            self.assertEqual(original_accounts, tuple(balance(work.external, day) for day in (DAY, NEXT)))
