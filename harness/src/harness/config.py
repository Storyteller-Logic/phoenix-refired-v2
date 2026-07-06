"""Harness configuration for brain connection."""

from pathlib import Path
import os

# Configuration default
CONFIG = {
    # Database configuration
    "brain_db_path": "/mnt/hdd/phoenix-refire/brain/live_brain.db",
    
    # Model endpoints
    "gemma_heretic_endpoint": "127.0.0.1:5810",
    "agents_a1_endpoint": "127.0.0.1:8000/v1",
    
    # Gates
    "min_correctness": 0.90,
    "ideal_correctness": 0.97,
    "ideal_correctness_parody": 1.0,
}

# Environment overrides
if "BTDBRAIN_DB_PATH" in os.environ:
    CONFIG["brain_db_path"] = os.environ["BTDBRAIN_DB_PATH"]