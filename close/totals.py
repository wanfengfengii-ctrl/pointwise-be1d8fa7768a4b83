from intake.observations import iter_effective_amounts


def total_for_day(connection, day):
    return sum(iter_effective_amounts(connection, day), start=0)
