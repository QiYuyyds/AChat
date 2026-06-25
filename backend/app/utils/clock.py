"""Wall-clock helpers.

The TypeScript backend stamps every row / event with ``Date.now()`` (epoch
milliseconds). Mirror that exactly so timestamps stay comparable across the
TS ↔ Python migration and so the frontend keeps receiving millisecond ints.
"""

import time


def now_ms() -> int:
    """Current epoch time in milliseconds (matches JS ``Date.now()``)."""
    return int(time.time() * 1000)
