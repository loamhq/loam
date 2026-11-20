"""Install the offline test guards in each Python child started with this directory on PYTHONPATH."""
import os
import sys

if os.environ.get("LOAM_OFFLINE"):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))
    import loam_offline
    loam_offline.block_network()
    sys.path.pop(0)
