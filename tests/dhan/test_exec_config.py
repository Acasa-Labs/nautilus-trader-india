"""Two switches, and the reason there are two.

A stray import cannot set an environment variable, and a stray environment
variable cannot construct a client. Either alone is a single point of failure
between a backtest and a real order.
"""

import pytest

from nautilus_india.dhan.config import (
    LIVE_ORDERS_ENV,
    DhanExecClientConfig,
    submission_refusal,
)


def test_both_switches_on_is_the_only_way_through():
    assert submission_refusal(
        DhanExecClientConfig(live_orders=True), {LIVE_ORDERS_ENV: "1"}
    ) is None


def test_the_config_flag_alone_is_not_enough():
    refusal = submission_refusal(DhanExecClientConfig(live_orders=True), {})
    assert refusal is not None
    assert LIVE_ORDERS_ENV in refusal


def test_the_environment_variable_alone_is_not_enough():
    refusal = submission_refusal(
        DhanExecClientConfig(live_orders=False), {LIVE_ORDERS_ENV: "1"}
    )
    assert refusal is not None
    assert "live_orders" in refusal


def test_neither_switch_names_both_of_them():
    """An operator reading this has to learn what is missing from one line."""
    refusal = submission_refusal(DhanExecClientConfig(), {})
    assert "live_orders" in refusal
    assert LIVE_ORDERS_ENV in refusal


@pytest.mark.parametrize("value", ["0", "", "true", "TRUE", "yes", "2", " 1", "1 "])
def test_the_variable_must_be_exactly_one(value):
    """`true`, `yes` and `0` are all things somebody types meaning something.
    One value, so the switch cannot be flipped by an approximation of it."""
    assert submission_refusal(
        DhanExecClientConfig(live_orders=True), {LIVE_ORDERS_ENV: value}
    ) is not None


def test_the_default_config_cannot_submit():
    """The safe direction is the default. A config nobody edited reads,
    reports, and sends nothing."""
    assert DhanExecClientConfig().live_orders is False


def test_the_config_holds_no_credential_by_default():
    """A Nautilus config is serialisable, and a serialised config with a token
    in it is a token in whatever wrote it out."""
    config = DhanExecClientConfig()
    assert config.access_token is None
    assert config.client_id is None


def test_the_refusal_is_a_reason_not_a_boolean():
    """It goes into an OrderDenied event, and 'denied' with no reason is a
    support ticket."""
    refusal = submission_refusal(DhanExecClientConfig(), {})
    assert len(refusal) > 60
