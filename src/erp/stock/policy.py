"""Pure stock-sync decisions. No I/O."""


def decide_listing_action(*, sellable_qty: int, safety_threshold: int) -> tuple[str, int]:
    """Choose pause vs quantity update.

    Published quantity is ``sellable_qty - safety_threshold``. When that is not
    positive the listing is paused instead of advertising the last units.
    """

    if sellable_qty < 0:
        raise ValueError("sellable_qty must be >= 0")
    if safety_threshold < 0:
        raise ValueError("safety_threshold must be >= 0")
    publishable = sellable_qty - safety_threshold
    if publishable <= 0:
        return "pause", 0
    return "update", publishable
