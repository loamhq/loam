"""Loaded by every Python process of a test run (tests/run-all puts this
directory first on PYTHONPATH): no socket leaves loopback. See
lib/loam_offline.py for why (the 2026-11-10 DNS outage)."""
import os
import sys

if os.environ.get("LOAM_OFFLINE"):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))
    import loam_offline
    loam_offline.block_network()
    sys.path.pop(0)
