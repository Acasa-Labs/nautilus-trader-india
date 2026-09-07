"""The package imports, and it does not drag a broker SDK in with it."""

import sys

import nautilus_india


def test_package_exposes_a_version():
    assert isinstance(nautilus_india.__version__, str)
    assert nautilus_india.__version__


def test_core_imports_no_broker_sdk_and_no_network_client():
    """`core` is offline by construction.

    This is not a style rule. If `core` can import a broker SDK it can be
    given one by accident, and a test that reaches the network is a test
    that fails on a train. Asserting on `sys.modules` catches the import
    at the moment it happens rather than the socket much later.
    """
    for module in ("kiteconnect", "dhanhq", "httpx", "websockets", "polars"):
        sys.modules.pop(module, None)

    import nautilus_india.core  # noqa: F401

    leaked = [
        m for m in ("kiteconnect", "dhanhq", "httpx", "websockets", "polars")
        if m in sys.modules
    ]
    assert leaked == [], f"nautilus_india.core pulled in {leaked}"
