import json
from tempfile import TemporaryDirectory

from audit.workspace import initialize_workspace, open_workspace
from close.finalize import seal
from dispatch.confirm import confirmation_frontier
from dispatch.submit import ReplyLost, advance
from intake.batches import apply
from meter_core import transaction
from receiver.balances import balance

DAYS = ("2025-03-01", "2025-03-02")
HEADS = ("march-01-second", "march-02-second")


def prepare(work):
    """Report the two base seals, then create the two unsent corrections."""
    for index, (day, initial, corrected) in enumerate(zip(DAYS, (5, 12), (2, 15)), 1):
        apply(work.local, day, f"initial-{index}", "initial", [("estuary", "intake", initial)])
        seal(work.local, day, f"first-{index}")
        advance(work.local, work.external, day)
        apply(work.local, day, f"correction-{index}", "correction", [("estuary", "intake", corrected)])
        seal(work.local, day, HEADS[index - 1])


def snapshot(work):
    with transaction(work.external):
        totals = tuple(balance(work.external, day).total for day in DAYS)
    with transaction(work.local):
        frontiers = tuple(confirmation_frontier(work.local, day) for day in DAYS)
    return totals, frontiers


def lose_reply(_receipt):
    raise ReplyLost("receiver committed before the reply was lost")


def demonstrate(root):
    initialize_workspace(root)
    with open_workspace(root) as work:
        prepare(work)
        before = snapshot(work)
        first = advance(work.local, work.external, DAYS[0], after_post=lose_reply)
        after_loss = snapshot(work)
    with open_workspace(root) as work:
        after_reopen = snapshot(work)
        advance(work.local, work.external, DAYS[1])
        after_second = snapshot(work)
        advance(work.local, work.external, DAYS[0])
        after_recovery = snapshot(work)
    actual = {
        "first_status": first.state,
        "before": before,
        "after_loss": after_loss,
        "after_reopen": after_reopen,
        "after_second": after_second,
        "after_recovery": after_recovery,
    }
    expected = {
        "first_status": "pending",
        "before": ((5, 12), (1, 1)),
        "after_loss": ((2, 12), (1, 1)),
        "after_reopen": ((2, 12), (1, 1)),
        "after_second": ((2, 15), (1, 2)),
        "after_recovery": ((2, 15), (2, 2)),
    }
    if actual != expected:
        raise AssertionError(f"single-day comparison: {actual!r} != {expected!r}")
    print(json.dumps(actual, sort_keys=True))


if __name__ == "__main__":
    with TemporaryDirectory(prefix="estuary-pair-example-") as root:
        demonstrate(root)
