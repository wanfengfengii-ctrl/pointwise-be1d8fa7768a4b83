import json
from pathlib import Path
from tempfile import TemporaryDirectory

from audit.lookup import lookup
from audit.purge import purge
from audit.reconcile import reconcile_posting
from audit.workspace import initialize_workspace, open_workspace
from close.finalize import seal
from dispatch.confirm import confirmation_frontier
from dispatch.submit import ReplyLost, advance, advance_pair
from intake.batches import apply
from pair_example import HEADS as PAIR_HEADS
from pair_example import DAYS as PAIR_DAYS
from pair_example import prepare as prepare_pair_fixture
from receiver.balances import balance
from receiver.receipts import receipt_for_request


def lose_reply(receipt):
    raise ReplyLost(f'reply for {receipt.request_id} lost after receiver commit')


def lose_pair_reply(pair_receipt):
    raise ReplyLost(
        f'pair reply for {pair_receipt.pair_id} lost after receiver commit'
    )


def demonstrate(root, sample):
    day = sample['day']
    other_day = sample['other_day']
    initialize_workspace(root)
    with open_workspace(root) as work:
        apply(work.local, day, 'east-initial', 'initial', sample['initial'])
        first = seal(work.local, day, 'east-1')
        advance(work.local, work.external, day)
        apply(work.local, day, 'east-correction-1', 'correction', [sample['corrections'][0]])
        second = seal(work.local, day, 'east-2')
        lost = advance(work.local, work.external, day, after_post=lose_reply)
        pending = lookup(work.local, second.seal_id)
        received = balance(work.external, day)
        original = receipt_for_request(work.external, received.postings[-1].request_id)
        apply(work.local, day, 'east-correction-2', 'correction', [sample['corrections'][1]])
        third = seal(work.local, day, 'east-3')
        removed = purge(work.local, sample['cutoff']).removed

    with open_workspace(root) as work:
        attempts = [advance(work.local, work.external, day) for _ in range(2)]
        reviewed = [lookup(work.local, revision.seal_id) for revision in (first, second, third)]
        account = balance(work.external, day)
        expected = {
            'sealed': [5, 12, 9], 'pending_status': 'pending', 'pending_delta': None,
            'pending_external_balance': 12, 'attempt_order': ['east-2', 'east-3'],
            'confirmed_deltas': [5, 7, -3], 'same_original_receipt': True,
        }
        actual = {
            'sealed': [item.revision.total if item else None for item in reviewed],
            'pending_status': pending.state, 'pending_delta': pending.delta,
            'pending_external_balance': received.total,
            'attempt_order': [attempt.seal_id for attempt in attempts],
            'confirmed_deltas': [item.delta if item else None for item in reviewed],
            'same_original_receipt': original is not None and attempts[0].receipt == original,
        }
        downstream_expected = {'balance': 9, 'posting_deltas': [5, 7, -3], 'purged_batches': 3}
        downstream_actual = {
            'balance': account.total, 'posting_deltas': [entry.delta for entry in account.postings],
            'purged_batches': len(removed),
        }
        if actual != expected or downstream_actual != downstream_expected:
            raise AssertionError(
                f'DAILY_DELTA_MISMATCH actual={actual!r} downstream={downstream_actual!r} '
                f'expected={expected!r} expected_downstream={downstream_expected!r}'
            )
        if lost.state != 'pending' or not reconcile_posting(work.external, reviewed[-1]).balance_matches:
            raise AssertionError('pending transition or final reconciliation failed')

        unchanged = seal(work.local, day, 'east-4')
        zero = advance(work.local, work.external, day)
        apply(work.local, other_day, 'west-initial', 'initial', sample['other_initial'])
        seal(work.local, other_day, 'west-1')
        west = advance(work.local, work.external, other_day)
        east_account = balance(work.external, day)
        west_account = balance(work.external, other_day)
        secondary_actual = {
            'zero_seal': zero.seal_id, 'zero_delta': zero.receipt.delta,
            'distinct_zero_receipt': zero.receipt != attempts[-1].receipt,
            'last_total': unchanged.total, 'east_balance': east_account.total,
            'east_deltas': [posting.delta for posting in east_account.postings],
            'west_balance': west_account.total, 'west_delta': west.receipt.delta,
        }
        secondary_expected = {
            'zero_seal': 'east-4', 'zero_delta': 0, 'distinct_zero_receipt': True,
            'last_total': 9, 'east_balance': 9, 'east_deltas': [5, 7, -3, 0],
            'west_balance': 4, 'west_delta': 4,
        }
        if secondary_actual != secondary_expected:
            raise AssertionError(f'closing ledger actual={secondary_actual!r} expected={secondary_expected!r}')

    evidence = {
        'case_id': 'v5.lost_reply_with_later_revision', 'input': sample,
        'expected': expected, 'actual': actual,
        'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
        'code_paths': [
            'intake/batches.py', 'close/finalize.py', 'dispatch/submit.py',
            'dispatch/confirm.py', 'receiver/requests.py', 'receiver/balances.py',
            'audit/lookup.py', 'audit/purge.py',
        ],
        'oracle': '2+3=5; 2+10=12; 2+7=9; adjacent differences are 5, 7, -3.',
    }
    print('POINTWISE_SCENARIO ' + json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    print(json.dumps(secondary_actual, ensure_ascii=False, sort_keys=True))


def demonstrate_paired_correction(root):
    """Two days booked together as -3/+3, with a lost reply and reopen reclaim."""
    pair_id = 'march-transfer'
    initialize_workspace(root)
    with open_workspace(root) as work:
        prepare_pair_fixture(work)
        before = tuple(balance(work.external, day).total for day in PAIR_DAYS)
        lost = advance_pair(
            work.local, work.external, pair_id, PAIR_HEADS, after_post=lose_pair_reply,
        )
        while_open = {
            'state': lost.state,
            'external_totals': tuple(balance(work.external, day).total for day in PAIR_DAYS),
            'lookup_states': [lookup(work.local, seal_id).state for seal_id in PAIR_HEADS],
        }
        original_receipts = tuple(
            receipt_for_request(work.external, f'pair:{pair_id}:{seal_id}')
            for seal_id in PAIR_HEADS
        )
    with open_workspace(root) as work:
        # Reopen and resubmit the same group in reverse order.
        recovered = advance_pair(
            work.local, work.external, pair_id, PAIR_HEADS[::-1],
        )
        replayed = advance_pair(work.local, work.external, pair_id, PAIR_HEADS)
        reconciliations = []
        for seal_id in PAIR_HEADS:
            review = lookup(work.local, seal_id)
            check = reconcile_posting(work.external, review)
            reconciliations.append({
                'seal_id': seal_id,
                'state': review.state,
                'delta': review.delta,
                'request_id': review.receipt.request_id,
                'posting_matches': check.posting_matches,
                'balance_matches': check.balance_matches,
            })
        after = {
            'state': recovered.state,
            'deltas': [receipt.delta for receipt in recovered.receipts],
            'external_totals': tuple(balance(work.external, day).total for day in PAIR_DAYS),
            'frontiers': tuple(
                confirmation_frontier(work.local, day) for day in PAIR_DAYS
            ),
            'posting_counts': [
                len(balance(work.external, day).postings) for day in PAIR_DAYS
            ],
            'same_original_receipts': recovered.receipts == original_receipts,
            'replay_unchanged': replayed.receipts == recovered.receipts,
            'reconciliations': reconciliations,
        }
        # Later revisions on both days keep using the ordinary single entry.
        apply(work.local, PAIR_DAYS[0], 'post-pair-1', 'correction',
              [('estuary', 'intake', 20)])
        seal(work.local, PAIR_DAYS[0], 'later-1')
        apply(work.local, PAIR_DAYS[1], 'post-pair-2', 'correction',
              [('estuary', 'intake', 30)])
        seal(work.local, PAIR_DAYS[1], 'later-2')
        later = (
            advance(work.local, work.external, PAIR_DAYS[0]).receipt.delta,
            advance(work.local, work.external, PAIR_DAYS[1]).receipt.delta,
        )
        final_totals = tuple(balance(work.external, day).total for day in PAIR_DAYS)

    expected_open = {
        'state': 'pending',
        'external_totals': (2, 15),
        'lookup_states': ['pending', 'pending'],
    }
    expected_after = {
        'state': 'confirmed',
        'deltas': [-3, 3],
        'external_totals': (2, 15),
        'frontiers': (2, 2),
        'posting_counts': [2, 2],
        'same_original_receipts': True,
        'replay_unchanged': True,
    }
    actual_open = while_open
    actual_after = {key: after[key] for key in expected_after}
    if before != (5, 12) or actual_open != expected_open or actual_after != expected_after:
        raise AssertionError(
            f'PAIR_DEMO_MISMATCH before={before!r} open={actual_open!r} '
            f'after={actual_after!r}'
        )
    if any(not item['posting_matches'] or not item['balance_matches']
           for item in reconciliations):
        raise AssertionError(f'pair reconciliation failed: {reconciliations!r}')
    if later != (18, 15) or final_totals != (20, 30):
        raise AssertionError(
            f'post-pair ordinary reporting failed: later={later!r} totals={final_totals!r}'
        )
    evidence = {
        'case_id': 'paired.two_day_correction',
        'base_totals': list(before),
        'after_lost_reply': actual_open,
        'after_reopen_reclaim': after,
        'reconciled_by_original_seal': reconciliations,
        'later_single_deltas': list(later),
        'final_totals': list(final_totals),
        'oracle': '5->2 is -3 and 12->15 is +3; the pair books both legs once.',
    }
    print('POINTWISE_PAIR ' + json.dumps(evidence, ensure_ascii=False, sort_keys=True))


def main():
    sample_path = Path(__file__).resolve().parent / 'samples' / 'shift.json'
    sample = json.loads(sample_path.read_text(encoding='utf-8'))
    with TemporaryDirectory(prefix='estuary-demo-') as root:
        demonstrate(root, sample)
    with TemporaryDirectory(prefix='estuary-pair-demo-') as root:
        demonstrate_paired_correction(root)


if __name__ == '__main__':
    main()
