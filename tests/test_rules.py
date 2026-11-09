import pytest

from audiorec.convert.probe import AudioInfo
from audiorec.convert.rules import EncodeParams, can_copy, encode_params, estimate_bytes, out_rate


def info(**kw):
    base = dict(codec="mp3", container="mp3", profile="mpeg1", rate=44100, bits=0,
                channels=2, bitrate_kbps=128, duration_s=10.0)
    return AudioInfo(**{**base, **kw})


def test_low_bitrate_mp3_is_copied():            assert can_copy(info(bitrate_kbps=128), "mp3")
def test_mpeg25_mp3_is_converted():               assert not can_copy(info(profile="mpeg25", rate=11025), "mp3")
def test_odd_rate_mp3_is_converted():             assert not can_copy(info(rate=32000), "mp3")
def test_aac_with_mp3_target_is_converted():      assert not can_copy(info(codec="aac", container="mp4", profile="lc"), "mp3")
def test_he_aac_is_converted():                   assert not can_copy(info(codec="aac", container="mp4", profile="he"), "aac")
def test_aac_256_is_copied():                     assert can_copy(info(codec="aac", container="mp4", profile="lc", bitrate_kbps=256), "aac")
def test_alac_is_never_copied_to_aac():           assert not can_copy(info(codec="alac", container="mp4", bits=16), "aac")
def test_wav_24_48k_is_copied():                  assert can_copy(info(codec="pcm", container="wav", bits=24, rate=48000), "wav")
def test_wav_96k_is_converted():                  assert not can_copy(info(codec="pcm", container="wav", bits=24, rate=96000), "wav")
def test_wav_8bit_and_float_are_converted():
    assert not can_copy(info(codec="pcm", container="wav", bits=8), "wav")
    assert not can_copy(info(codec="float", container="wav", bits=32), "wav")
def test_wav_with_aiff_target_is_converted():     assert not can_copy(info(codec="pcm", container="wav", bits=16), "aiff")
def test_mp3_with_wav_target_is_converted():      assert not can_copy(info(), "wav")
def test_multichannel_wav_is_converted():
    assert not can_copy(info(codec="pcm", container="wav", bits=24, rate=48000, channels=6), "wav")
def test_multichannel_mp3_is_converted():         assert not can_copy(info(channels=6), "mp3")


@pytest.mark.parametrize("src,out", [(44100, 44100), (48000, 48000), (88200, 44100), (176400, 44100),
                                     (96000, 48000), (192000, 48000), (32000, 44100), (22050, 44100)])
def test_out_rate(src, out): assert out_rate(src) == out


def test_encode_params_caps_depth_and_channels():
    assert encode_params(info(codec="flac", container="other", bits=24, rate=96000, channels=6), "wav") == EncodeParams(48000, 2, 24)
    assert encode_params(info(codec="float", container="wav", bits=32, channels=1), "aiff") == EncodeParams(44100, 1, 24)
    assert encode_params(info(codec="pcm", container="wav", bits=8), "wav").bits == 16
    assert encode_params(info(codec="flac", bits=24), "mp3").bits == 0


def test_estimate_bytes():
    assert estimate_bytes(info(), "wav", src_size=1234, copy=True) == 1234
    assert estimate_bytes(info(duration_s=10.0), "mp3", src_size=0, copy=False) == 400_000
    hi_res = info(codec="pcm", container="wav", bits=24, rate=96000, channels=2, duration_s=10.0)
    assert estimate_bytes(hi_res, "wav", src_size=0, copy=False) == 2_880_000
