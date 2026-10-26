from audiorec.mp3sink import mp3_rate_message


def test_mp3_rate_message():
    assert mp3_rate_message("Speakers", 96000) == (
        "'Speakers' runs at 96000 Hz, MP3 only supports 32/44.1/48 kHz.\n"
        "Set it to 48000 Hz: Sound settings > device > Properties > Advanced."
    )
