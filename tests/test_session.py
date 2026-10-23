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


def test_write_not_blocked_by_slow_metadata_write(tmp_path, monkeypatch):
    import threading

    s = Session.create(tmp_path, 48000, "d")
    entered, release = threading.Event(), threading.Event()

    def slow_meta(fields):
        entered.set()
        release.wait(5)

    monkeypatch.setattr(s, "_write_meta", slow_meta)
    t = threading.Thread(target=s.update_fields, kwargs={"title": "x"})
    t.start()
    assert entered.wait(5)
    w = threading.Thread(target=s.write, args=(stereo([1, 2, 3]),))
    w.start()
    w.join(2)
    finished = not w.is_alive()
    release.set()
    t.join(5)
    s.close()
    assert finished, "write() blocked behind a metadata write"
    assert s.frames == 3 and s.fields["title"] == "x"


def test_frames_view_matches_read_frames_and_is_read_only(tmp_path):
    s = Session.create(tmp_path, 48000, "d")
    s.write(stereo(np.arange(100)))
    v = s.frames_view(10, 30)
    assert v.dtype == np.int16 and v.shape == (20, 2)
    assert np.array_equal(v, s.read_frames(10, 30))
    assert not v.flags.writeable
    del v
    s.delete()
    assert not s.folder.exists()        # the mapping was released, so Windows let it go


def test_frames_view_clamps(tmp_path):
    s = Session.create(tmp_path, 48000, "d")
    s.write(stereo(np.arange(10)))
    assert np.array_equal(s.frames_view(-5, 100), s.read_frames(0, 10))
    assert s.frames_view(8, 3).shape == (0, 2)
    assert s.frames_view(50, 60).shape == (0, 2)
    s.close()


def test_frames_view_empty_file(tmp_path):
    s = Session.create(tmp_path, 48000, "d")
    v = s.frames_view(0, 100)
    assert v.shape == (0, 2) and v.dtype == np.int16


def test_find_leftovers_skips_non_object_session_json(tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "session.json").write_text("[]")
    (bad / "audio.pcm").write_bytes(b"\0" * 8)
    assert find_leftovers(tmp_path) == []
    assert (bad / "audio.pcm").exists()
