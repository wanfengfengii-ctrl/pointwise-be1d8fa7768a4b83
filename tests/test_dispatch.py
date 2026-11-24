import sqlite3
from dataclasses import replace

from audit.lookup import lookup
from audit.workspace import initialize_workspace, open_workspace
from close.finalize import seal
from dispatch.confirm import confirmation_frontier, confirm_receipt
from dispatch.outbound import request_for_revision
from dispatch.submit import advance
from meter_core import ConflictError, EvidenceError, InputError
from receiver.balances import balance
from receiver.receipts import receipt_for_request
from receiver.requests import post
from tests.support import (
    DAY, NEXT, WorkspaceTestCase, block_commit, correct_and_seal, lose_reply, rejected_commit, seed,
)


class DispatchTests(WorkspaceTestCase):
    def test_signed_deltas(self):
        with self.open() as work:
            seed(work.local)
            correct_and_seal(work.local, 'second', 10, 'v2')
            correct_and_seal(work.local, 'third', 7, 'v3')
            seal(work.local, DAY, 'v4')
            results = [advance(work.local, work.external, DAY) for _ in range(4)]
            self.assertEqual([(item.state, item.seal_id) for item in results], [
                ('confirmed', 'v1'), ('confirmed', 'v2'), ('confirmed', 'v3'), ('confirmed', 'v4'),
            ])
            self.assertEqual([result.receipt.delta for result in results], [5, 7, -3, 0])
            account = balance(work.external, DAY)
            self.assertEqual(account.total, 9)
            self.assertEqual([entry.delta for entry in account.postings], [5, 7, -3, 0])
            self.assertEqual(advance(work.local, work.external, DAY).state, 'idle')

    def test_zero_receipt(self):
        with self.open() as work:
            seed(work.local)
            original = advance(work.local, work.external, DAY).receipt
            seal(work.local, DAY, 'unchanged')
            zero = advance(work.local, work.external, DAY).receipt
            self.assertEqual(zero.delta, 0)
            self.assertNotEqual(zero.request_id, original.request_id)
            self.assertNotEqual(zero.receipt_id, original.receipt_id)
            self.assertEqual(lookup(work.local, 'unchanged').receipt, zero)
            account = balance(work.external, DAY)
            self.assertEqual(account.total, 5)
            self.assertEqual([entry.request_id for entry in account.postings], [original.request_id, zero.request_id])

    def test_unknown_receipt(self):
        for failure in ('lost_reply', 'local_commit'):
            with self.subTest(failure=failure):
                root = self.root / failure
                initialize_workspace(root)
                with open_workspace(root) as work:
                    seed(work.local)
                    self.assertEqual(advance(work.local, work.external, DAY).receipt.delta, 5)
                    correct_and_seal(work.local, 'second', 10, 'v2')
                    if failure == 'lost_reply':
                        result = advance(work.local, work.external, DAY, after_post=lose_reply)
                        self.assertEqual((result.state, result.seal_id, result.receipt), ('pending', 'v2', None))
                    else:
                        def fail_after_remote_commit(_):
                            block_commit(work.local)

                        try:
                            with self.assertRaises(sqlite3.DatabaseError):
                                advance(work.local, work.external, DAY, after_post=fail_after_remote_commit)
                        finally:
                            work.local.set_authorizer(None)
                    review = lookup(work.local, 'v2')
                    self.assertEqual((review.state, review.receipt, review.delta), ('pending', None, None))
                    self.assertEqual(confirmation_frontier(work.local, DAY), 1)
                    bound = request_for_revision(work.local, 'v2')
                    self.assertEqual((bound.day, bound.sequence, bound.delta), (DAY, 2, 7))
                    original = receipt_for_request(work.external, bound.request_id)
                    self.assertIsNotNone(original)
                    self.assertEqual(balance(work.external, DAY).total, 12)
                    correct_and_seal(work.local, 'third', 7, 'v3')
                    self.assertIsNone(request_for_revision(work.local, 'v3'))
                with open_workspace(root) as work:
                    recovered = advance(work.local, work.external, DAY)
                    following = advance(work.local, work.external, DAY)
                    account = balance(work.external, DAY)
                    actual = (recovered.seal_id, following.seal_id,
                              following.receipt.delta if following.receipt else None, account.total)
                    expected = ('v2', 'v3', -3, 9)
                    self.assertEqual(actual, expected, f'DAILY_DELTA_MISMATCH actual={actual!r} expected={expected!r}')
                    self.assertEqual(recovered.receipt, original)
                    self.assertEqual(lookup(work.local, 'v2').receipt, original)
                    self.assertEqual([entry.delta for entry in account.postings], [5, 7, -3])
                    self.assertEqual(advance(work.local, work.external, DAY).state, 'idle')

    def test_receiver_conflict(self):
        with self.open() as work:
            original = post(work.external, 'direct-a', DAY, 5)
            self.assertEqual(post(work.external, 'direct-a', DAY, 5), original)
            for day, delta in ((DAY, 7), (NEXT, 5)):
                with self.subTest(day=day, delta=delta), self.assertRaises(ConflictError):
                    post(work.external, 'direct-a', day, delta)
            with self.assertRaises(InputError):
                post(work.external, 'invalid', DAY, True)
            with self.assertRaises(InputError):
                balance(work.external, '20250301')
            self.assertEqual(balance(work.external, DAY).total, 5)
            self.assertEqual(len(balance(work.external, DAY).postings), 1)
            self.assertEqual(balance(work.external, NEXT).total, 0)
            self.assertEqual(receipt_for_request(work.external, 'direct-a'), original)

    def test_stable_receipt_after_later_post(self):
        with self.open() as work:
            original = post(work.external, 'direct-a', DAY, 5)
            later = post(work.external, 'direct-b', DAY, 7)
            self.assertNotEqual(original.receipt_id, later.receipt_id)
            with self.assertRaises(ConflictError):
                post(work.external, 'direct-a', DAY, 12)
        with self.open() as work:
            self.assertEqual(post(work.external, 'direct-a', DAY, 5), original)
            account = balance(work.external, DAY)
            self.assertEqual(account.total, 12)
            self.assertEqual([entry.sequence for entry in account.postings], [original.posting_sequence, later.posting_sequence])

    def test_binding_and_receiver_commit_failures(self):
        with self.open() as work:
            seed(work.local)
            with rejected_commit(work.local), self.assertRaises(sqlite3.DatabaseError):
                advance(work.local, work.external, DAY)
            self.assertIsNone(request_for_revision(work.local, 'v1'))
            self.assertEqual(balance(work.external, DAY).postings, ())
            with rejected_commit(work.external), self.assertRaises(sqlite3.DatabaseError):
                advance(work.local, work.external, DAY)
            bound = request_for_revision(work.local, 'v1')
            self.assertIsNotNone(bound)
            self.assertIsNone(receipt_for_request(work.external, bound.request_id))
            self.assertEqual(balance(work.external, DAY).postings, ())
            self.assertEqual(lookup(work.local, 'v1').state, 'pending')
        with self.open() as work:
            confirmed = advance(work.local, work.external, DAY).receipt
            self.assertEqual((confirmed.request_id, confirmed.delta), (bound.request_id, 5))
            self.assertEqual(len(balance(work.external, DAY).postings), 1)

    def test_mismatched_receipt_does_not_confirm(self):
        with self.open() as work:
            seed(work.local)
            advance(work.local, work.external, DAY, after_post=lose_reply)
            request = request_for_revision(work.local, 'v1')
            original = receipt_for_request(work.external, request.request_id)
            for mismatched in (replace(original, delta=4), replace(original, day=NEXT),
                               replace(original, request_id='another-request')):
                with self.subTest(receipt=mismatched), self.assertRaises(EvidenceError):
                    confirm_receipt(work.local, request, mismatched)
            self.assertEqual(confirmation_frontier(work.local, DAY), 0)
            self.assertEqual(lookup(work.local, 'v1').state, 'pending')
            self.assertEqual(advance(work.local, work.external, DAY).receipt, original)

    def test_interleaved_workers_do_not_rewind_frontier(self):
        with self.open() as first, self.open() as second:
            seed(first.local)
            correct_and_seal(first.local, 'second', 10, 'v2')

            def run_other_worker(receipt):
                retry = advance(second.local, second.external, DAY)
                self.assertEqual((retry.seal_id, retry.receipt), ('v1', receipt))
                following = advance(second.local, second.external, DAY)
                self.assertEqual((following.seal_id, following.receipt.delta), ('v2', 7))

            original = advance(first.local, first.external, DAY, after_post=run_other_worker)
            self.assertEqual((original.seal_id, original.receipt.delta), ('v1', 5))
            self.assertEqual(confirmation_frontier(first.local, DAY), 2)
            self.assertEqual(advance(first.local, first.external, DAY).state, 'idle')
            self.assertEqual([item.delta for item in balance(first.external, DAY).postings], [5, 7])
