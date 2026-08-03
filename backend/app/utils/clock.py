"""Wall-clock helpers.

The TypeScript backend stamps every row / event with ``Date.now()`` (epoch
milliseconds). Mirror that exactly so timestamps stay comparable across the
TS ↔ Python migration and so the frontend keeps receiving millisecond ints.
"""

import time

_last_ts: int = 0


def now_ms() -> int:
    """Current epoch time in milliseconds (matches JS ``Date.now()``).

    Guarantees monotonically increasing return values within a single process:
    if the wall clock hasn't advanced since the last call, the returned value
    is ``last + 1``. This prevents timestamp collisions that would break
    message ordering and time-window deletes in concurrent scenarios.
    """
    global _last_ts
    ts = int(time.time() * 1000)
    if ts <= _last_ts:
        ts = _last_ts + 1
    _last_ts = ts
    return ts
