"""Qt-free state machine for the recorder window.

The window calls next_state() on every event and applies controls_for()
to its widgets. Keeping this pure makes the button rules testable without
a display.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class State(Enum):
    READY = "ready"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPED = "stopped"
    SAVING = "saving"


class Event(Enum):
    MAIN = "main"
    STOP = "stop"
    SAVE = "save"
    SAVED = "saved"
    DISCARDED = "discarded"
    CAPTURE_ENDED = "capture_ended"
    EXPORT_FAILED = "export_failed"


_TRANSITIONS: dict[tuple[State, Event], State] = {
    (State.READY, Event.MAIN): State.RECORDING,
    (State.RECORDING, Event.MAIN): State.PAUSED,
    (State.PAUSED, Event.MAIN): State.RECORDING,
    (State.RECORDING, Event.STOP): State.STOPPED,
    (State.PAUSED, Event.STOP): State.STOPPED,
    (State.RECORDING, Event.CAPTURE_ENDED): State.STOPPED,
    (State.PAUSED, Event.CAPTURE_ENDED): State.STOPPED,
    (State.STOPPED, Event.SAVE): State.SAVING,
    (State.SAVING, Event.SAVED): State.READY,
    (State.SAVING, Event.EXPORT_FAILED): State.STOPPED,
    (State.STOPPED, Event.DISCARDED): State.READY,
}


def next_state(state: State, event: Event) -> State:
    """Return the next state, or `state` unchanged for an event it does not handle."""
    return _TRANSITIONS.get((state, event), state)


@dataclass(frozen=True)
class Controls:
    main_icon: Literal["record", "pause", "play"]
    main_enabled: bool
    stop_enabled: bool
    source_enabled: bool
    format_enabled: bool
    save_panel_enabled: bool
    save_enabled: bool


def controls_for(
    state: State,
    *,
    selection_ok: bool,
    format_ok: bool,
    playing: bool = False,
) -> Controls:
    """Widget enablement for a state. `playing` only affects STOPPED."""
    if state is State.READY:
        return Controls("record", True, False, True, True, False, False)
    if state is State.RECORDING:
        return Controls("pause", True, True, False, False, False, False)
    if state is State.PAUSED:
        return Controls("record", True, True, False, False, False, False)
    if state is State.STOPPED:
        return Controls(
            "pause" if playing else "play",
            selection_ok,
            False,
            False,
            True,
            True,
            selection_ok and format_ok,
        )
    return Controls("play", False, False, False, False, False, False)
