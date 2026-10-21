import json
from datetime import datetime, timedelta

import numpy as np

from audiorec.session import Session, default_root, find_leftovers


def stereo(mono) -> bytes:
    a = np.asarray(mono, dtype=np.int16)
    return np.column_stack([a, a]).tobytes()


def test_default_root(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_root() == tmp_path / "AudioRecorder" / "sessions"


def test_session_roundtrip(tmp_path):
    s = Session.create(tmp_path, 48000, "Speakers")
    s.write(stereo(np.arange(100)))
    s.update_fields(title="Song", artist="Artist")
    s.close()
    r = Session.open(s.folder)
    assert r.frames == 100 and r.rate == 48000 and r.fields["title"] == "Song"
    assert r.device_name == "Speakers"
    assert r.started == s.started
    assert r.read_frames(10, 20)[:, 0].tolist() == list(range(10, 20))


def test_create_makes_root_and_unique_folders(tmp_path):
    root = tmp_path / "a" / "b"
    now = datetime(2026, 1, 2, 3, 4, 5)
    s1 = Session.create(root, 44100, "d", now=now)
    s2 = Session.create(root, 44100, "d", now=now)
    assert s1.folder.name == "20260102_030405"
    assert s2.folder.name == "20260102_030405_2"
    assert json.loads((s1.folder / "session.json").read_text())["started"] == now.isoformat()
    assert s1.fields == {}


def test_session_iter_frames_clamps(tmp_path):
    s = Session.create(tmp_path, 48000, "d")
    s.write(stereo(np.arange(10)))
    chunks = list(s.iter_frames(-5, 100, chunk_frames=4))
    assert [len(c) for c in chunks] == [4, 4, 2]
    assert np.concatenate(chunks)[:, 0].tolist() == list(range(10))
    assert list(s.iter_frames(8, 3)) == []
    assert s.read_frames(50, 60).shape == (0, 2)
    s.close()


def test_find_leftovers_skips_and_removes_empty(tmp_path):
    now = datetime(2026, 1, 1, 12, 0, 0)
    old = Session.create(tmp_path, 48000, "d", now=now)
    old.write(stereo([1, 2, 3]))
    old.close()
    new = Session.create(tmp_path, 48000, "d", now=now + timedelta(hours=1))
    new.write(stereo([1]))
    new.close()
    empty = Session.create(tmp_path, 48000, "d", now=now + timedelta(hours=2))
    empty.close()
    (tmp_path / "junk").mkdir()
    (tmp_path / "junk" / "x.txt").write_text("hi")
    got = find_leftovers(tmp_path)
    assert [g.folder for g in got] == [new.folder, old.folder]
    assert not empty.folder.exists()
    assert (tmp_path / "junk").exists()
    assert find_leftovers(tmp_path / "missing") == []


def test_session_survives_unclosed_writer(tmp_path):
    s = Session.create(tmp_path, 48000, "d")
    s.write(stereo(np.arange(7)))
    assert Session.open(s.folder).frames == 7
    s.close()


def test_delete_removes_folder(tmp_path):
    s = Session.create(tmp_path, 48000, "d")
    s.write(stereo([1]))
    s.delete()
    assert not s.folder.exists()
