from __future__ import annotations

import itertools

import pytest

from audiorec.gui.state import Controls, Event, State, controls_for, next_state

TRANSITIONS = [
    (State.READY, Event.MAIN, State.RECORDING),
    (State.RECORDING, Event.MAIN, State.PAUSED),
    (State.PAUSED, Event.MAIN, State.RECORDING),
    (State.RECORDING, Event.STOP, State.STOPPED),
    (State.PAUSED, Event.STOP, State.STOPPED),
    (State.RECORDING, Event.CAPTURE_ENDED, State.STOPPED),
    (State.PAUSED, Event.CAPTURE_ENDED, State.STOPPED),
    (State.STOPPED, Event.SAVE, State.SAVING),
    (State.SAVING, Event.SAVED, State.READY),
    (State.SAVING, Event.EXPORT_FAILED, State.STOPPED),
    (State.STOPPED, Event.DISCARDED, State.READY),
]


def test_event_enum_members():
    assert [e.name for e in Event] == [
        "MAIN",
        "STOP",
        "SAVE",
        "SAVED",
        "DISCARDED",
        "CAPTURE_ENDED",
        "EXPORT_FAILED",
    ]


@pytest.mark.parametrize(
    "state, selection_ok, format_ok, playing, expected",
    [
        (State.READY, True, True, False,
         Controls("record", True, False, True, True, False, False)),
        (State.RECORDING, True, True, False,
         Controls("pause", True, True, False, False, False, False)),
        (State.PAUSED, True, True, False,
         Controls("record", True, True, False, False, False, False)),
        (State.STOPPED, True, True, False,
         Controls("play", True, False, False, True, True, True)),
        (State.SAVING, True, True, False,
         Controls("play", False, False, False, False, False, False)),
    ],
    ids=["READY", "RECORDING", "PAUSED", "STOPPED", "SAVING"],
)
def test_controls_table(state, selection_ok, format_ok, playing, expected):
    assert controls_for(
        state, selection_ok=selection_ok, format_ok=format_ok, playing=playing
    ) == expected


def test_controls_stopped_playing_shows_pause_icon():
    controls = controls_for(State.STOPPED, selection_ok=True, format_ok=True, playing=True)
    assert controls.main_icon == "pause"


def test_controls_playing_ignored_outside_stopped():
    for state in (State.READY, State.RECORDING, State.PAUSED, State.SAVING):
        playing_on = controls_for(state, selection_ok=True, format_ok=True, playing=True)
        playing_off = controls_for(state, selection_ok=True, format_ok=True, playing=False)
        assert playing_on == playing_off


def test_controls_stopped_too_short():
    controls = controls_for(State.STOPPED, selection_ok=False, format_ok=True)
    assert controls.main_enabled is False
    assert controls.save_enabled is False
    assert controls.format_enabled is True
    assert controls.save_panel_enabled is True


def test_controls_stopped_mp3_not_allowed():
    controls = controls_for(State.STOPPED, selection_ok=True, format_ok=False)
    assert controls.save_enabled is False
    assert controls.format_enabled is True
    assert controls.main_enabled is True


@pytest.mark.parametrize("state, event, expected", TRANSITIONS)
def test_transitions(state, event, expected):
    assert next_state(state, event) is expected


def test_invalid_transition_is_noop():
    listed = set((s, e) for s, e, _ in TRANSITIONS)
    for state, event in itertools.product(State, Event):
        if (state, event) in listed:
            continue
        assert next_state(state, event) is state, (state, event)


def test_stopped_main_is_not_a_transition():
    # Playback is handled outside the machine.
    assert next_state(State.STOPPED, Event.MAIN) is State.STOPPED
