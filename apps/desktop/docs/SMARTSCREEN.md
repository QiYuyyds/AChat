# Unsigned v1 and Windows SmartScreen

AChat desktop v1 may ship **without code signing**.

## What users may see

- “Windows protected your PC” (SmartScreen)
- “Unknown publisher”

## How to proceed (internal / early adopters)

1. Click **More info**
2. Click **Run anyway**
3. If SmartScreen blocks the installer, right-click → Properties → Unblock (when present), then run again

Engineering releases for internal testing accept this trade-off. Production public distribution should add Authenticode signing in a later change.

This note fulfills distribution task 9.4.
