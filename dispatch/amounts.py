def signed_delta(total: int, previous_total: int) -> int:
    """Return the adjustment from the confirmed daily total to this seal."""
    return total - previous_total
