def pytest_configure(config):
    # Registered so the opt-out in test_travel.py's no_live_bridge fixture doesn't warn.
    config.addinivalue_line(
        "markers",
        "real_bridge_helper: test exercises a controller bridge helper itself, so the "
        "hermetic no_live_bridge fixture must not stub it out",
    )
