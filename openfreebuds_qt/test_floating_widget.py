import sys
import pytest
from PyQt6.QtWidgets import QApplication
from openfreebuds import IOpenFreebuds
from openfreebuds_qt.app.widget.floating_widget import OfbQtFloatingWidget


class MockOfb:
    STATE_CONNECTED = IOpenFreebuds.STATE_CONNECTED
    STATE_DISCONNECTED = IOpenFreebuds.STATE_DISCONNECTED

    def __init__(self):
        self.state = self.STATE_CONNECTED
        self.props = {
            ("battery", None): {"left": 80, "right": 85, "case": 100},
            ("anc", None): {"mode": "normal", "mode_options": "normal,awareness,cancellation"},
            ("sound", None): {
                "equalizer_preset": "equalizer_preset_default",
                "equalizer_preset_options": "equalizer_preset_default,equalizer_preset_hardbass,equalizer_preset_treble,equalizer_preset_voices",
            },
        }

    async def get_state(self):
        return self.state

    async def get_device_tags(self):
        return "HUAWEI FreeBuds SE 5 MAX", "00:11:22:33:44:55"

    async def get_property(self, group, prop=None):
        return self.props.get((group, prop))

    async def set_property(self, group, prop, value):
        self.props[(group, prop)] = value
        if (group, None) in self.props and isinstance(self.props[(group, None)], dict):
            self.props[(group, None)][prop] = value

    async def send_message(self, kind, *args):
        pass


class MockContext:
    def __init__(self):
        self.ofb = MockOfb()
        self.tray = None


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.mark.asyncio
async def test_floating_widget_creation_and_update(qapp):
    ctx = MockContext()
    widget = OfbQtFloatingWidget(ctx)
    assert widget is not None
    assert widget.width() == 290
    assert "btnMicMute" == widget.btn_mic_mute.objectName()
    assert "btnSpeakerMute" == widget.btn_speaker_mute.objectName()
    assert len(widget.anc_buttons) == 3

    # Test update_ui
    await widget.update_ui()
    assert widget.title_label.text() == "HUAWEI FreeBuds SE 5 MAX"
    assert widget.batt_items["left"][1].text() == "80%"
    assert widget.batt_items["right"][1].text() == "85%"
    assert widget.batt_items["case"][1].text() == "100%"

    # Test clicking ANC
    await widget.on_anc_clicked("cancellation")
    assert widget._current_anc_mode == "cancellation"

    # Test clicking button directly via Qt signal
    widget.anc_buttons["awareness"].click()
    import asyncio
    await asyncio.sleep(0.05)
    assert widget._current_anc_mode == "awareness"

    # Test visibility toggle
    widget.show_widget()
    assert widget.isVisible()
    widget.hide_widget()
    assert not widget.isVisible()


@pytest.mark.asyncio
async def test_floating_widget_eq_submenu(qapp):
    ctx = MockContext()
    widget = OfbQtFloatingWidget(ctx)
    await widget.update_ui()
    widget.show()
    qapp.processEvents()

    # Submenu starts collapsed
    assert not widget._is_expanded
    assert not widget.submenu_container.isVisible()
    assert widget.btn_expand.text() == "▾"
    collapsed_height = widget.height()

    # Clicking expand arrow opens the submenu
    widget.show_widget()
    widget.btn_expand.click()
    assert widget._is_expanded
    assert widget.submenu_container.isVisible()
    assert widget.btn_expand.text() == "▴"
    assert widget.height() > collapsed_height

    # Current preset is displayed on the button
    assert widget.btn_eq_preset.isEnabled()
    assert "Default" in widget.btn_eq_preset.text()

    # Populating menu lists all presets and marks the active one
    widget._populate_eq_menu()
    actions = widget.eq_menu.actions()
    assert len(actions) == 4
    assert actions[0].isChecked()
    assert not actions[1].isChecked()

    # Selecting another preset updates device property and button label
    await widget.on_eq_preset_selected("equalizer_preset_hardbass")
    assert widget._current_eq_preset == "equalizer_preset_hardbass"
    assert ctx.ofb.props[("sound", "equalizer_preset")] == "equalizer_preset_hardbass"
    assert "Bass-boost" in widget.btn_eq_preset.text()

    # Clicking expand arrow again collapses the submenu
    widget.btn_expand.click()
    assert not widget._is_expanded
    assert not widget.submenu_container.isVisible()
    assert widget.btn_expand.text() == "▾"
    assert widget.height() == collapsed_height

    # Disconnected state disables preset selector
    ctx.ofb.state = MockOfb.STATE_DISCONNECTED
    await widget.update_ui()
    assert not widget.btn_eq_preset.isEnabled()
    widget.hide_widget()


@pytest.mark.asyncio
async def test_floating_widget_resize(qapp):
    from PyQt6.QtCore import QPoint

    ctx = MockContext()
    widget = OfbQtFloatingWidget(ctx)
    await widget.update_ui()
    widget.show()
    qapp.processEvents()

    # Check bounds when collapsed
    assert widget.minimumWidth() == widget.MIN_WIDTH
    assert widget.maximumWidth() == widget.MAX_WIDTH
    assert widget.minimumHeight() == widget.MIN_COLLAPSED_HEIGHT
    assert widget.maximumHeight() == widget.MAX_COLLAPSED_HEIGHT

    # Resize to minimum
    widget.resize(widget.MIN_WIDTH, widget.MIN_COLLAPSED_HEIGHT)
    qapp.processEvents()
    assert widget.width() == widget.MIN_WIDTH
    assert widget.height() == widget.MIN_COLLAPSED_HEIGHT
    small_font_size = widget._batt_font_size
    assert small_font_size < 12

    # Resize to maximum
    widget.resize(widget.MAX_WIDTH, widget.MAX_COLLAPSED_HEIGHT)
    qapp.processEvents()
    assert widget.width() == widget.MAX_WIDTH
    assert widget.height() == widget.MAX_COLLAPSED_HEIGHT
    large_font_size = widget._batt_font_size
    assert large_font_size > small_font_size

    # Clamping beyond min/max
    widget.resize(100, 100)
    qapp.processEvents()
    assert widget.width() == widget.MIN_WIDTH
    assert widget.height() == widget.MIN_COLLAPSED_HEIGHT

    widget.resize(600, 600)
    qapp.processEvents()
    assert widget.width() == widget.MAX_WIDTH
    assert widget.height() == widget.MAX_COLLAPSED_HEIGHT

    # Edge/corner hit-testing
    assert widget._hit_test_edges(QPoint(2, 2)) == (widget._EDGE_LEFT | widget._EDGE_TOP)
    assert widget._hit_test_edges(QPoint(widget.width() - 2, widget.height() - 2)) == (
        widget._EDGE_RIGHT | widget._EDGE_BOTTOM
    )
    assert widget._hit_test_edges(QPoint(widget.width() // 2, widget.height() // 2)) == 0

    # Expanding updates height bounds and keeps width
    widget.resize(320, 250)
    qapp.processEvents()
    widget.toggle_submenu()
    assert widget._is_expanded
    assert widget.width() == 320
    assert widget.minimumHeight() == widget.MIN_EXPANDED_HEIGHT
    assert widget.maximumHeight() == widget.MAX_EXPANDED_HEIGHT
    widget.toggle_submenu()
    assert not widget._is_expanded
    assert widget.width() == 320

