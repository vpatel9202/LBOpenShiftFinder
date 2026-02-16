"""Manage persistent sync state for tracking which shifts are on Google Calendar."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.models import SyncState

logger = logging.getLogger(__name__)

STATE_FILE = Path(__file__).parent.parent / "state" / "synced_shifts.json"


def load_state() -> SyncState:
    """Load the sync state from disk."""
    if not STATE_FILE.exists():
        logger.info("No existing state file found, starting fresh")
        return SyncState()

    try:
        data = STATE_FILE.read_text(encoding="utf-8")
        state = SyncState.from_json(data)
        logger.info(
            f"Loaded state: {len(state.synced_shifts)} open, "
            f"{len(state.picked_shifts)} picked, {len(state.scheduled_shifts)} scheduled"
        )
        return state
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Corrupted state file, starting fresh: {e}")
        return SyncState()


def save_state(state: SyncState) -> None:
    """Save the sync state to disk."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(state.to_json(), encoding="utf-8")
    logger.info(
        f"Saved state: {len(state.synced_shifts)} open, "
        f"{len(state.picked_shifts)} picked, {len(state.scheduled_shifts)} scheduled"
    )
