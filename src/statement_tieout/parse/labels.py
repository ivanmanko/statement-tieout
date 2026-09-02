"""The summary-label vocabulary (SPEC §7.5), shared by the header reader and
the heuristic profiler — the latter uses it to recognise section headings.

Longest label first, so `deposits and credits` is not consumed by `deposits`.
This module is the one place the vocabulary lives; SPEC §7.5 mirrors it, and
extending one requires editing both in the same commit.
"""

BEGINNING_LABELS = ("beginning balance", "previous balance", "opening balance",
                    "balance forward")
ENDING_LABELS = ("ending balance", "new balance", "closing balance")
DEPOSIT_LABELS = ("deposits and credits", "total deposits", "deposits", "credits", "additions")
WITHDRAWAL_LABELS = ("withdrawals and debits", "total withdrawals", "withdrawals", "debits",
                     "subtractions")
