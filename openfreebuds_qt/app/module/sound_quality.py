import asyncio
import json

from PyQt6.QtCore import Qt, QRectF, QTimer, QSize
from PyQt6.QtGui import QIcon, QPainter, QPen, QColor, QBrush, QFont
from PyQt6.QtWidgets import QSlider, QMenu, QInputDialog, QMessageBox, QFileDialog, QLabel, QWidget
from qasync import asyncSlot

from openfreebuds.exceptions import OfbTooManyItemsError
from openfreebuds.utils.logger import create_logger
from openfreebuds_backend import get_active_bluetooth_audio_codec
from openfreebuds_qt.app.module.common import OfbQtCommonModule
from openfreebuds_qt.config import OfbQtConfigParser
from openfreebuds_qt.designer.sound_quality import Ui_OfbQtSoundQualityModule
from openfreebuds_qt.qt_i18n import get_eq_preset_names
from openfreebuds_qt.utils import get_img_colored
from openfreebuds_qt.utils.async_dialog import run_dialog_async
from openfreebuds_qt.utils.core_event import OfbCoreEvent
from openfreebuds_qt.utils.qt_utils import fill_combo_box, blocked_signals, qt_error_handler

log = create_logger("OfbQtSoundQualityModule")


class OfbQtCodecPill(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = True
        self._angle = 0
        self._text = ""
        self.setFixedHeight(22)
        self.setFixedWidth(42)
        self.setToolTip(self.tr("Detecting Bluetooth audio codec…"))
        self.setVisible(True)

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(33)  # ~30 FPS
        self._anim_timer.timeout.connect(self._rotate)
        self._anim_timer.start()

    def sizeHint(self):
        return QSize(self.width(), 22)

    def minimumSizeHint(self):
        return QSize(42, 22)

    def _rotate(self):
        if self._loading:
            self._angle = (self._angle + 10) % 360
            self.update()

    def set_loading(self, tooltip=""):
        if not self._loading:
            self._loading = True
            self._text = ""
            self.setFixedWidth(42)
            if not self._anim_timer.isActive():
                self._anim_timer.start()
        self.setToolTip(tooltip or self.tr("Detecting Bluetooth audio codec…"))
        self.setVisible(True)
        self.update()

    def set_codec(self, text: str, tooltip: str = ""):
        if self._loading or self._text != text:
            self._loading = False
            self._text = text
            if self._anim_timer.isActive():
                self._anim_timer.stop()
            fm = self.fontMetrics()
            w = fm.horizontalAdvance(text) + 24
            self.setFixedWidth(max(w, 42))
        self.setToolTip(tooltip)
        self.setVisible(True)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setBrush(QBrush(QColor("#2d2d2d")))
        p.setPen(QPen(QColor("#444444"), 1))
        p.drawRoundedRect(rect, 9, 9)

        if self._loading:
            size = 11
            cx = self.width() / 2
            cy = self.height() / 2
            arc_rect = QRectF(cx - size / 2, cy - size / 2, size, size)

            # Subtle background circle track
            p.setPen(QPen(QColor(255, 255, 255, 35), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(arc_rect)

            # Spinning white arc
            pen = QPen(QColor("#ffffff"), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(arc_rect, int(self._angle * 16), int(100 * 16))
        else:
            p.setPen(QColor("#ffffff"))
            font = p.font()
            font.setPointSize(9)
            font.setBold(True)
            p.setFont(font)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)


class OfbQtSoundQualityModule(Ui_OfbQtSoundQualityModule, OfbQtCommonModule):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.eq_preset_option_names = get_eq_preset_names()

        self.menu_actions = [
            (self.tr("New preset…"), self.new_preset),
            (self.tr("Delete…"), self.delete_preset),
            (None, None),
            (self.tr("Export to file…"), self.export_file),
            (self.tr("Load file…"), self.load_file),
        ]

        self.dialog_preset_copy = QMessageBox(QMessageBox.Icon.Information,
                             self.tr("Notice"),
                             self.tr("This preset isn't available in your device, it will be created as "
                                     "custom preset."),
                             QMessageBox.StandardButton.Ok)
        self.dialog_preset_copy.setWindowModality(Qt.WindowModality.WindowModal)

        self.dialog_too_many_presets = QMessageBox(QMessageBox.Icon.Critical,
                             self.tr("Failed"),
                             self.tr("Can't create: too many custom preset created in device."),
                             QMessageBox.StandardButton.Ok)
        self.dialog_too_many_presets.setWindowModality(Qt.WindowModality.WindowModal)

        self._eq_last_options: list[str] = []
        self._eq_rows: list[QSlider] = []
        self._last_preset_data: list[int] = []
        self._last_copy_options: list[str] = []

        self.setupUi(self)

        self.undo_btn.setIcon(
            QIcon(get_img_colored("undo", self.palette().text().color().getRgb()))
        )

        self.custom_menu = QMenu()
        self.custom_edit_button.setMenu(self.custom_menu)
        for name, action in self.menu_actions:
            if name is None:
                self.custom_menu.addSeparator()
            else:
                # noinspection PyUnresolvedReferences
                self.custom_menu.addAction(name).triggered.connect(action)

        for i in range(10):
            self._add_slider(i)
        self.custom_eq.setVisible(False)

        # Setup custom codec pill with loading spinner (instant loading)
        old_pill = self.codec_pill
        self.codec_pill = OfbQtCodecPill(self.sq_root)
        self.sq_header_layout.replaceWidget(old_pill, self.codec_pill)
        old_pill.deleteLater()
        self.codec_pill.setVisible(True)
        self.codec_pill.set_loading()

        self._device_address = None
        try:
            self._device_address = OfbQtConfigParser.get_instance().get("device", "address")
        except Exception:
            pass

        self._codec_timer = QTimer(self)
        self._codec_timer.setInterval(1500)
        self._codec_timer.timeout.connect(self._check_codec)
        self._codec_timer.start()

        self._check_codec()

    def _add_slider(self, i):
        lock = asyncio.Lock()

        @asyncSlot(int)
        async def _on_change(value: int):
            if lock.locked():
                return
            async with lock:
                self._last_preset_data[i] = value
                await self.ofb.set_property("sound", "equalizer_rows",
                                            json.dumps(self._last_preset_data))

        slider = QSlider(Qt.Orientation.Vertical, self.custom_eq_rows)
        slider.setRange(-60, 60)
        slider.setTickPosition(QSlider.TickPosition.TicksBothSides)
        slider.setTickInterval(5)
        slider.setToolTip("0")
        # noinspection PyUnresolvedReferences
        slider.valueChanged.connect(_on_change)
        self.custom_eq_rows_layout.addWidget(slider)
        self._eq_rows.append(slider)

    async def update_ui(self, event: OfbCoreEvent):
        sound = await self.ofb.get_property("sound")
        self.list_item.setVisible(sound is not None)
        if sound is None:
            return

        self.custom_edit_button.setVisible(int(sound.get("equalizer_max_custom_modes", "0")) > 0)
        self.save_panel.setVisible(not json.loads(sound.get("equalizer_saved", "true")))

        if event.is_changed("sound", "quality_preference"):
            value = sound.get("quality_preference")
            self.sq_root.setVisible(value is not None)
            if value == "sqp_connectivity":
                self.sq_radio_connection.setChecked(True)
            elif value == "sqp_quality":
                self.sq_radio_sound.setChecked(True)
            else:
                self.sq_radio_sound.setChecked(False)
                self.sq_radio_connection.setChecked(False)

        if event.is_changed("sound", "equalizer_preset"):
            value = sound.get("equalizer_preset")
            options = sound.get("equalizer_preset_options")
            copy_options = sound.get("equalizer_preset_create_options")
            self.eq_root.setVisible(value is not None and options is not None)
            if copy_options is not None:
                self._last_copy_options = list(copy_options.split(","))
            if options is not None:
                self._eq_last_options = list(options.split(","))
                fill_combo_box(self.eq_preset_box, self._eq_last_options, self.eq_preset_option_names, value)

        if event.is_changed("sound", "equalizer_rows"):
            rows = json.loads(sound.get("equalizer_rows") or "null")
            self.custom_eq.setVisible(rows is not None)
            if rows is not None:
                self._last_preset_data = rows
                for i, value in enumerate(rows):
                    with blocked_signals(self._eq_rows[i]):
                        self._eq_rows[i].setValue(value)
                        self._eq_rows[i].setToolTip(str(value))

        await self._update_codec_pill()

    def showEvent(self, event):
        super().showEvent(event)
        self.codec_pill.setVisible(True)
        self._check_codec()

    def _check_codec(self):
        self.codec_pill.setVisible(True)
        if not self._device_address:
            try:
                self._device_address = OfbQtConfigParser.get_instance().get("device", "address")
            except Exception:
                pass

        codec_info = get_active_bluetooth_audio_codec(self._device_address)
        if codec_info:
            self.codec_pill.set_codec(codec_info["label"], codec_info["tooltip"])
            if hasattr(self, "_codec_timer") and self._codec_timer.interval() != 4000:
                self._codec_timer.setInterval(4000)
        else:
            self.codec_pill.set_loading()
            if hasattr(self, "_codec_timer") and self._codec_timer.interval() != 1500:
                self._codec_timer.setInterval(1500)

    async def _update_codec_pill(self):
        try:
            _, addr = await self.ofb.get_device_tags()
            if addr:
                self._device_address = addr
        except Exception:
            pass
        self._check_codec()

    @asyncSlot()
    async def on_custom_save(self):
        async with qt_error_handler("OfbQtSoundQualityModule_EqCustomSave", self.ctx):
            await self.ofb.set_property("sound", "equalizer_saved", "true")

    @asyncSlot()
    async def on_custom_reset(self):
        async with qt_error_handler("OfbQtSoundQualityModule_EqCustomReset", self.ctx):
            await self.ofb.set_property("sound", "equalizer_saved", "false")

    @asyncSlot()
    async def on_sq_set_connectivity(self):
        async with qt_error_handler("OfbQtSoundQualityModule_SetConnectivity", self.ctx):
            await self.ofb.set_property("sound", "quality_preference", "sqp_connectivity")

    @asyncSlot()
    async def on_sq_set_quality(self):
        async with qt_error_handler("OfbQtSoundQualityModule_SetQuality", self.ctx):
            await self.ofb.set_property("sound", "quality_preference", "sqp_quality")

    @asyncSlot(int)
    async def on_eq_preset_change(self, index: int):
        async with qt_error_handler("OfbQtSoundQualityModule_ChangePreset", self.ctx):
            value = self._eq_last_options[index]

            if value in self._last_copy_options:
                self.dialog_preset_copy.show()

            try:
                await self.ofb.set_property("sound", "equalizer_preset", value)
            except OfbTooManyItemsError:
                self.dialog_too_many_presets.show()

    @asyncSlot()
    async def new_preset(self):
        async with qt_error_handler("OfbQtSoundQualityModule_NewPreset", self.ctx):
            try:
                dialog = QInputDialog(self)
                dialog.setWindowModality(Qt.WindowModality.WindowModal)
                dialog.setInputMode(QInputDialog.InputMode.TextInput)
                dialog.setWindowTitle(self.tr("Create new equalizer preset"))
                dialog.setLabelText(self.tr("Enter new preset name:"))
                if not await run_dialog_async(dialog):
                    return False

                log.info(f"Create new mode with name {dialog.textValue()}")
                await self.ofb.set_property("sound", "equalizer_preset", dialog.textValue())
                return True
            except OfbTooManyItemsError:
                self.dialog_too_many_presets.show()
                return False

    @asyncSlot()
    async def delete_preset(self):
        if await self.ofb.get_property("sound", "equalizer_rows") is None:
            return

        async with qt_error_handler("OfbQtSoundQualityModule_DeletePreset", self.ctx):
            dialog = QMessageBox(QMessageBox.Icon.Question,
                                 self.tr("Delete equalizer mode?"),
                                 self.tr("Will delete following mode:") + "\n" + self.eq_preset_box.currentText(),
                                 QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                                 self)
            dialog.setWindowModality(Qt.WindowModality.WindowModal)
            if not await run_dialog_async(dialog):
                return

            log.info(f"Delete mode with name {self.eq_preset_box.currentText()}")
            await self.ofb.set_property("sound", "equalizer_rows", "null")

    @asyncSlot()
    async def export_file(self):
        if await self.ofb.get_property("sound", "equalizer_rows") is None:
            return

        async with qt_error_handler("OfbQtSoundQualityModule_ExportFile", self.ctx):
            dialog = QFileDialog(self, self.tr("Save equalizer preset to file…"))
            dialog.setWindowModality(Qt.WindowModality.WindowModal)
            dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
            if not await run_dialog_async(dialog):
                return
            paths = dialog.selectedFiles()
            if len(paths) < 1:
                return

            with open(paths[0], "w") as f:
                f.write(await self.ofb.get_property("sound", "equalizer_rows"))

    @asyncSlot()
    async def load_file(self):
        async with qt_error_handler("OfbQtSoundQualityModule_LoadFile", self.ctx):
            dialog = QFileDialog(self, self.tr("Load equalizer preset from file…"))
            dialog.setWindowModality(Qt.WindowModality.WindowModal)
            dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
            if not await run_dialog_async(dialog):
                return
            paths = dialog.selectedFiles()
            if len(paths) < 1:
                return

            with open(paths[0], "r") as f:
                data = f.read()

            if not await self.new_preset():
                return
            await asyncio.sleep(0.5)
            await self.ofb.set_property("sound", "equalizer_rows", data)
            await asyncio.sleep(0.25)
            await self.ofb.set_property("sound", "equalizer_saved", "true")
