import sqlite3

from audit.lookup import lookup
from close.finalize import seal
from dispatch.outbound import request_for_revision
from dispatch.submit import advance
from intake.batches import apply, latest_batch_sequence
from meter_core import ConflictError, InputError
from receiver.balances import balance
from tests.support import DAY, NEXT, WorkspaceTestCase, correct_and_seal, rejected_commit, seed


class CloseTests(WorkspaceTestCase):
    def test_effective_totals(self):
        with self.open() as work:
            first = seed(work.local)
            second = correct_and_seal(work.local, 'amend', 10, 'v2')
            self.assertEqual((first.total, second.total), (5, 12))
            self.assertEqual((first.sequence, second.sequence), (1, 2))
            self.assertEqual((first.source_watermark, second.source_watermark), (1, 2))
            self.assertEqual(lookup(work.local, 'v1').revision, first)

    def test_exact_integers(self):
        large = 1 << 18000
        with self.open() as work:
            empty = seal(work.local, DAY, 'empty')
            self.assertEqual((empty.total, empty.source_watermark), (0, 0))
            apply(work.local, DAY, 'large', 'initial', [
                ('east', 'zero', 0), ('east', 'large', large), ('west', 'one', 1),
            ])
            filled = seal(work.local, DAY, 'filled')
            self.assertTrue(filled.total == large + 1)
            result = apply(work.local, DAY, 'to-zero', 'correction', [('east', 'large', 0)])
            self.assertEqual(result.changed, 1)
            reduced = seal(work.local, DAY, 'reduced')
            self.assertEqual(reduced.total, 1)
            self.assertEqual(advance(work.local, work.external, DAY).receipt.delta, 0)
            self.assertTrue(advance(work.local, work.external, DAY).receipt.delta == large + 1)
            self.assertTrue(advance(work.local, work.external, DAY).receipt.delta == -large)
            self.assertEqual(balance(work.external, DAY).total, 1)
        with self.open() as work:
            self.assertTrue(lookup(work.local, 'filled').revision.total == large + 1)
            self.assertTrue(lookup(work.local, 'reduced').delta == -large)
            self.assertEqual(balance(work.external, DAY).total, 1)

    def test_conflicting_batch(self):
        with self.open() as work:
            rows = [('east', 'a', 2), ('east', 'b', 3)]
            original = apply(work.local, DAY, 'initial', 'initial', rows)
            self.assertEqual(apply(work.local, DAY, 'initial', 'initial', rows), original)
            for day, kind, parameters in (
                (DAY, 'initial', [('east', 'a', 2), ('east', 'b', 10)]),
                (DAY, 'initial', rows[::-1]), (NEXT, 'initial', rows), (DAY, 'correction', rows),
            ):
                with self.subTest(day=day, kind=kind, parameters=parameters):
                    with self.assertRaises(ConflictError):
                        apply(work.local, day, 'initial', kind, parameters)
            self.assertEqual(seal(work.local, DAY, 'v1').total, 5)
            self.assertEqual(latest_batch_sequence(work.local, DAY), 1)
            self.assertEqual(latest_batch_sequence(work.local, NEXT), 0)

    def test_same_batch_replay(self):
        with self.open() as work:
            seed(work.local)
            original_rows = [('east', 'a', 2), ('east', 'b', 10)]
            original = apply(work.local, DAY, 'amend', 'correction', original_rows)
            self.assertEqual((original.sequence, original.changed), (2, 1))
            correct_and_seal(work.local, 'later', 7, 'v2')
            self.assertEqual(apply(work.local, DAY, 'amend', 'correction', original_rows), original)
            self.assertEqual(latest_batch_sequence(work.local, DAY), 3)
            self.assertEqual(seal(work.local, DAY, 'v3').total, 9)

    def test_seal_rollback(self):
        with self.open() as work:
            apply(work.local, DAY, 'initial', 'initial', [('east', 'a', 5)])
            with rejected_commit(work.local), self.assertRaises(sqlite3.DatabaseError):
                seal(work.local, DAY, 'rolled-back')
        with self.open() as work:
            self.assertIsNone(lookup(work.local, 'rolled-back'))
            self.assertIsNone(request_for_revision(work.local, 'rolled-back'))
            self.assertEqual(advance(work.local, work.external, DAY).state, 'idle')
            self.assertEqual(balance(work.external, DAY).postings, ())
            self.assertEqual(seal(work.local, DAY, 'rolled-back').sequence, 1)

    def test_duplicate_seal_id(self):
        with self.open() as work:
            first = seed(work.local)
            with self.assertRaises(ConflictError):
                seal(work.local, DAY, first.seal_id)
            with self.assertRaises(ConflictError):
                seal(work.local, NEXT, first.seal_id)
            self.assertEqual(lookup(work.local, first.seal_id).revision, first)
            self.assertEqual(advance(work.local, work.external, NEXT).state, 'idle')
            self.assertEqual(advance(work.local, work.external, DAY).receipt.delta, 5)
            self.assertEqual(advance(work.local, work.external, DAY).state, 'idle')
            self.assertEqual(seal(work.local, DAY, 'v2').sequence, 2)

    def test_invalid_inputs_and_partial_updates_roll_back(self):
        with self.open() as work:
            seed(work.local)
            bad_rows = [
                [('east', 'new', True)], [('east', 'new', None)], [('east', 'new', 1.5)],
                [('east', 'new')], [('', 'new', 1)], [('east', 'new', 1), ('east', 'new', 2)],
            ]
            for rows in bad_rows:
                with self.subTest(rows=rows), self.assertRaises(InputError):
                    apply(work.local, DAY, 'invalid', 'initial', rows)
            with self.assertRaises(InputError):
                apply(work.local, '20250301', 'invalid', 'initial', [])
            with self.assertRaises(InputError):
                seal(work.local, '2025-02-30', 'invalid-day')
            with self.assertRaises(ConflictError):
                apply(work.local, DAY, 'invalid', 'correction', [('east', 'a', 12), ('east', 'absent', 4)])
            with self.assertRaises(ConflictError):
                apply(work.local, DAY, 'invalid', 'initial', [('east', 'new', 4), ('east', 'a', 12)])
            with rejected_commit(work.local), self.assertRaises(sqlite3.DatabaseError):
                apply(work.local, DAY, 'invalid', 'correction', [('east', 'a', 12)])
            self.assertEqual(seal(work.local, DAY, 'still-original').total, 5)
            self.assertEqual(latest_batch_sequence(work.local, DAY), 1)
            self.assertEqual(apply(work.local, DAY, 'invalid', 'initial', [('east', 'new', 4)]).sequence, 2)
