"""Integration tests package.

Tests in this package make real network requests and launch a headless browser.
They are excluded from the standard ``uv run pytest`` run and must be invoked
explicitly::

    uv run pytest -m integration

"""
