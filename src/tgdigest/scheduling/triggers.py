"""Pure trigger-evaluation logic (unit-testable without a scheduler)."""

from __future__ import annotations


def evaluate_time_trigger(unprocessed: int, min_messages: int, send_empty: bool) -> bool:
    """Whether a time-based digest should run now.

    Runs when enough new messages have accumulated; if ``send_empty`` is set, a
    run also happens below the minimum so an "empty" digest is still produced.
    """
    if unprocessed >= min_messages:
        return True
    return send_empty


def evaluate_count_trigger(unprocessed: int, max_messages: int) -> bool:
    """Whether the message-count threshold has been reached for an immediate run."""
    return max_messages > 0 and unprocessed >= max_messages
