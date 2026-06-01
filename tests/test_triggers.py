from __future__ import annotations

from tgdigest.scheduling.triggers import evaluate_count_trigger, evaluate_time_trigger


def test_time_trigger():
    assert evaluate_time_trigger(5, 3, send_empty=False) is True
    assert evaluate_time_trigger(1, 3, send_empty=False) is False
    assert evaluate_time_trigger(1, 3, send_empty=True) is True


def test_count_trigger():
    assert evaluate_count_trigger(3, 3) is True
    assert evaluate_count_trigger(2, 3) is False
    assert evaluate_count_trigger(99, 0) is False
