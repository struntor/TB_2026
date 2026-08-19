# Strategy Comparison

This module compares two execution variants for the same engine trade:

1. a buyable underlying proxy
2. a ZEBRA options structure

## Default Mapping

- `SPX` -> underlying proxy `SPY`
- `SPX` -> options root `SPXW`

## ZEBRA Heuristic

- long signal -> call ZEBRA
- short signal -> put ZEBRA
- entry expiration -> first expiration at or after `entry_date + target_dte`
- strike selection -> choose the leg pair whose mid premium is closest to a zero-extrinsic back-ratio structure

The comparison output writes:

- per-trade CSV
- per-symbol summary CSV
- engine trade export for traceability
- primary options metrics follow `use_mid_prices` from the config
- mid and conservative option valuations are both written for inspection
