"""The summary-label vocabulary (SPEC §7.5), shared by the header reader and
the heuristic profiler — the latter uses it to recognise section headings.

Longest label first, so `deposits and credits` is not consumed by `deposits`.
This module is the one place the vocabulary lives; SPEC §7.5 mirrors it, and
extending one requires editing both in the same commit.
"""

BEGINNING_LABELS = ("prior statement balance", "beginning balance", "previous balance",
                    "opening balance", "balance forward", "prior balance")
ENDING_LABELS = ("ending statement balance", "ending balance", "new balance",
                 "closing balance")
DEPOSIT_LABELS = ("total deposits/credits", "deposits and credits", "deposits/credits",
                  "total deposits", "deposits", "credits", "additions")
WITHDRAWAL_LABELS = ("total checks/debits", "withdrawals and debits", "checks/debits",
                     "total withdrawals", "withdrawals", "debits", "subtractions",
                     "other debits", "checks")

#: Statements that state their cycle as a pair of lines rather than a range
#: (SPEC §7.16): the last statement's date opens this period, this one's closes it.
CYCLE_START_LABEL = "last statement"
CYCLE_END_LABEL = "this statement"
