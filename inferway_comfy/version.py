"""The package version, sent to the Inferway API in the User-Agent.

Kept equal to ``version`` in pyproject.toml and to PLUGIN_VERSION in
web/inferway_update.js; tests/check_ux.py pins all three.
"""

PLUGIN_VERSION = "0.1.6"
USER_AGENT = f"inferway-comfy/{PLUGIN_VERSION}"
