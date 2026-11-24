from datetime import date

from meter_core import InputError


def parse_day(value):
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            pass
        else:
            if parsed.isoformat() == value:
                return value
    raise InputError('day must be a calendar date in YYYY-MM-DD form')
