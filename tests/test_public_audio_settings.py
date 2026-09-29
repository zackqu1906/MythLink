import pytest
from test_interaction_controls import _controller, _close


@pytest.mark.parametrize('previous', ['pcm', 'opus', 'adpcm', 'invalid'])
def test_saved_audio_settings_migrate_to_public_adpcm(tmp_path, monkeypatch, previous):
    from PySide6.QtCore import QSettings
    import proximic_ring.ui.controller as module
    path = str(tmp_path / 'settings.ini')
    settings = QSettings(path, QSettings.IniFormat)
    settings.setValue('ring/audioEncoding', previous)
    settings.setValue('ring/audioEncodingDefaultVersion', 2)
    settings.sync()
    monkeypatch.setattr(module, 'QSettings', lambda *args: QSettings(path, QSettings.IniFormat))
    controller = _controller(tmp_path, monkeypatch)
    try:
        controller._selector = 'test-ring-uuid'
        assert controller.audioEncoding == 'adpcm'
        assert controller._runtime_settings().encoding == 'adpcm'
        assert not controller._runtime_settings().collect_imu
        assert controller._settings.value('ring/audioEncoding') == 'adpcm'
        assert controller._settings.value('ring/audioEncodingDefaultVersion') == 3
        controller.audioEncoding = 'opus'
        assert controller.audioEncoding == 'adpcm'
    finally:
        _close(controller)
