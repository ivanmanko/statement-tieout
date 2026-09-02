"""Reconciliation: the free, deterministic oracle this project is built around."""

from .diagnose import Diagnosis, diagnose
from .engine import (
    BALANCE_EQUATION,
    CHECKS,
    DEPOSITS_COUNT,
    DEPOSITS_TOTAL,
    PRINTED_BLOCK_CLOSES,
    WITHDRAWALS_COUNT,
    WITHDRAWALS_TOTAL,
    reconcile,
)

__all__ = [
    "BALANCE_EQUATION",
    "CHECKS",
    "DEPOSITS_COUNT",
    "DEPOSITS_TOTAL",
    "PRINTED_BLOCK_CLOSES",
    "WITHDRAWALS_COUNT",
    "WITHDRAWALS_TOTAL",
    "Diagnosis",
    "diagnose",
    "reconcile",
]
