from dataclasses import dataclass

from close.calendar import parse_day
from receiver.ledger import Posting, read_day_postings


@dataclass(frozen=True, slots=True)
class DayBalance:
    day: str
    total: int
    postings: tuple[Posting, ...]


def balance(connection, day):
    day = parse_day(day)
    postings = read_day_postings(connection, day)
    return DayBalance(day, sum(posting.delta for posting in postings), postings)
