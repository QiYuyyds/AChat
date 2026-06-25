"""Platform detection.

Port of src/server/platform.ts. All platform branches should go through these
two constants instead of scattering ``sys.platform == "win32"`` checks: one
source of truth, and easy to grep.
"""

import sys

IS_WINDOWS = sys.platform == "win32"
IS_POSIX = not IS_WINDOWS
