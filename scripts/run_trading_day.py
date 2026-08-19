from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import sys
from typing import Sequence


BOT_ROOT = Path(__file__).resolve().parents[1]
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

from src.bot.daily_orchestrator import ET

# Import the existing supervisor entry point from the same scripts directory.
# The manual launcher only fixes its execution mode to one ET session; all
# pipeline construction and safety checks remain in one implementation.
from run_trading_bot_24_7 import (
    main as supervisor_main,
    parse_args as parse_supervisor_args,
)


def parse_manual_args(arguments: Sequence[str], session_day: date):
    return parse_supervisor_args(arguments, fixed_session_day=session_day)


def main() -> None:
    session_day = datetime.now(tz=ET).date()
    supervisor_main(sys.argv[1:], fixed_session_day=session_day)


if __name__ == "__main__":
    main()
