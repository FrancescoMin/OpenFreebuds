import openfreebuds_backend as ob


def test_audio_backend_mute_exports():
    assert hasattr(ob, "get_microphone_mute")
    assert hasattr(ob, "set_microphone_mute")
    assert hasattr(ob, "toggle_microphone_mute")
    assert hasattr(ob, "get_speaker_mute")
    assert hasattr(ob, "set_speaker_mute")
    assert hasattr(ob, "toggle_speaker_mute")

    # Functions should return booleans and not raise unhandled exceptions
    mic_muted = ob.get_microphone_mute()
    assert isinstance(mic_muted, bool)

    speaker_muted = ob.get_speaker_mute()
    assert isinstance(speaker_muted, bool)


def test_shortcuts_have_new_actions():
    from openfreebuds.shortcuts import OfbShortcuts
    all_shortcuts = OfbShortcuts.all()
    assert "toggle_floating_widget" in all_shortcuts
    assert "toggle_mic_mute" in all_shortcuts
    assert "toggle_speaker_mute" in all_shortcuts
