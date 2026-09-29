"""A-share order minimums; see docs/EXECUTION_ASSUMPTIONS.md."""

import math


def order_unit(symbol: str, fallback: int = 100) -> tuple[int, int]:
    code = str(symbol).split(".")[0]
    return (200, 1) if code.startswith(("688", "689")) else (fallback, fallback)


def round_order(quantity: float, symbol: str, fallback: int = 100, *, holding: int | None = None) -> int:
    minimum, step = order_unit(symbol, fallback)
    if holding is not None and quantity >= holding:
        return holding  # A remaining odd lot can be sold in full.
    rounded = int(math.floor(max(0, quantity) / step) * step)
    return rounded if rounded >= minimum else 0
