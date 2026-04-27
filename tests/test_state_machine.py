"""Unit tests for the order state machine transitions."""

import pytest

from app.models import ORDER_TRANSITIONS, OrderStatus


@pytest.mark.parametrize(
    "from_status, to_status, expected",
    [
        # Happy paths
        (OrderStatus.PENDING, OrderStatus.AWAITING_PAYMENT, True),
        (OrderStatus.PENDING, OrderStatus.CANCELLED, True),
        (OrderStatus.AWAITING_PAYMENT, OrderStatus.PAID, True),
        (OrderStatus.AWAITING_PAYMENT, OrderStatus.PAYMENT_FAILED, True),
        (OrderStatus.PAYMENT_FAILED, OrderStatus.AWAITING_PAYMENT, True),
        (OrderStatus.PAYMENT_FAILED, OrderStatus.CANCELLED, True),
        (OrderStatus.PAID, OrderStatus.FULFILLED, True),
        (OrderStatus.PAID, OrderStatus.CANCELLED, True),
        # Compliance REVIEW
        (OrderStatus.PENDING, OrderStatus.REVIEW, True),
        (OrderStatus.REVIEW, OrderStatus.AWAITING_PAYMENT, True),   # approved
        (OrderStatus.REVIEW, OrderStatus.CANCELLED, True),           # rejected
        (OrderStatus.REVIEW, OrderStatus.PAID, False),               # must go via AWAITING_PAYMENT
        (OrderStatus.AWAITING_PAYMENT, OrderStatus.REVIEW, False),   # can't re-flag after payment
        # Refund
        (OrderStatus.PAID, OrderStatus.REFUNDED, True),
        (OrderStatus.FULFILLED, OrderStatus.REFUNDED, False),        # too late
        (OrderStatus.AWAITING_PAYMENT, OrderStatus.REFUNDED, False), # not charged yet
        # Terminal states — nothing allowed
        (OrderStatus.FULFILLED, OrderStatus.CANCELLED, False),
        (OrderStatus.FULFILLED, OrderStatus.PAID, False),
        (OrderStatus.CANCELLED, OrderStatus.PENDING, False),
        (OrderStatus.CANCELLED, OrderStatus.PAID, False),
        (OrderStatus.REFUNDED, OrderStatus.PAID, False),
        (OrderStatus.REFUNDED, OrderStatus.CANCELLED, False),
        # Backwards / invalid
        (OrderStatus.PAID, OrderStatus.PENDING, False),
        (OrderStatus.AWAITING_PAYMENT, OrderStatus.PENDING, False),
    ],
)
def test_transitions(from_status: OrderStatus, to_status: OrderStatus, expected: bool):
    allowed = to_status in ORDER_TRANSITIONS.get(from_status, set())
    assert allowed == expected, (
        f"Expected {from_status} → {to_status} to be "
        f"{'allowed' if expected else 'blocked'}"
    )


def test_terminal_states_have_no_transitions():
    assert ORDER_TRANSITIONS[OrderStatus.FULFILLED] == set()
    assert ORDER_TRANSITIONS[OrderStatus.CANCELLED] == set()
    assert ORDER_TRANSITIONS[OrderStatus.REFUNDED] == set()


def test_all_statuses_covered():
    """Every OrderStatus must appear as a key in ORDER_TRANSITIONS."""
    for status in OrderStatus:
        assert status in ORDER_TRANSITIONS, f"{status} missing from ORDER_TRANSITIONS"
