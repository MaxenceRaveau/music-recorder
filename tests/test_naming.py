from __future__ import annotations

from datetime import datetime

import pytest

from audiorec.naming import build_filename, is_valid_filename, sanitize, with_extension

T = datetime(2026, 1, 1)


def test_build_filename_artist_title():
    assert build_filename("Daft Punk", "One More Time", "mp3", T) == "Daft Punk - One More Time.mp3"


def test_build_filename_only_title():
    assert build_filename("", "Song", "flac", T) == "Song.flac"


def test_build_filename_only_artist():
    assert build_filename("Artist", "", "wav", T) == "Artist.wav"


def test_build_filename_empty_fallback():
    assert build_filename(" ", "", "mp3", datetime(2026, 10, 21, 9, 5, 3)) == "Recording 20261021_090503.mp3"


def test_build_filename_illegal_chars():
    assert build_filename("AC/DC", "What?", "mp3", T) == "AC_DC - What_.mp3"


def test_build_filename_strips_whitespace_before_deciding_empty():
    assert build_filename("  Artist  ", "   ", "wav", T) == "Artist.wav"


def test_build_filename_fallback_when_stem_sanitizes_to_nothing():
    assert build_filename("", "...", "mp3", datetime(2026, 10, 21, 9, 5, 3)) == "Recording 20261021_090503.mp3"


def test_build_filename_long_stem_capped_at_180():
    stem = build_filename("A" * 300, "", "mp3", T)[: -len(".mp3")]
    assert len(stem) == 180


def test_sanitize_trailing_dot_and_reserved():
    assert sanitize("Song. ") == "Song" and sanitize("CON") == "_CON"


def test_sanitize_reserved_names_case_insensitive_and_with_extension():
    assert sanitize("nul") == "_nul"
    assert sanitize("com1") == "_com1"
    assert sanitize("LPT9") == "_LPT9"
    assert sanitize("CON.txt") == "_CON.txt"


def test_sanitize_not_reserved_when_only_prefix_matches():
    assert sanitize("CONCERT") == "CONCERT"
    assert sanitize("COM10") == "COM10"


def test_sanitize_replaces_control_chars_and_all_illegal_chars():
    assert sanitize('a<b>c:d"e\\f|g?h*i\x00j\x1fk') == "a_b_c_d_e_f_g_h_i_j_k"


def test_sanitize_cap_then_strip_never_ends_in_dot_or_space():
    result = sanitize("x" * 179 + ". . .")
    assert len(result) <= 180
    assert not result.endswith((".", " "))


def test_with_extension_swaps_known_ext():
    assert with_extension("a - b.mp3", "flac") == "a - b.flac"
    assert with_extension("v1.2 mix", "wav") == "v1.2 mix.wav"


def test_with_extension_known_ext_case_insensitive():
    assert with_extension("a - b.MP3", "wav") == "a - b.wav"


@pytest.mark.parametrize("name", ["One More Time", "Song.mp3", "a..b.flac", "Été"])
def test_is_valid_filename_accepts_plain_names(name):
    assert is_valid_filename(name)


@pytest.mark.parametrize("name", ["", "AC/DC - TNT", "What?", r"..\x", r"a\b", r"C:\x.mp3",
                                  "..", ".", 'say "hi"', "a|b", "a*b", "<x>"])
def test_is_valid_filename_rejects_paths_and_illegal_chars(name):
    assert not is_valid_filename(name)
