import asyncio
from typing import Optional

from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QGuiApplication, QIcon, QMouseEvent, QResizeEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qasync import asyncSlot

from openfreebuds import IOpenFreebuds, OfbEventKind
from openfreebuds.utils.logger import create_logger
import openfreebuds_backend
from openfreebuds_qt.config import OfbQtConfigParser
from openfreebuds_qt.generic import IOfbQtApplication
from openfreebuds_qt.qt_i18n import get_eq_preset_names
from openfreebuds_qt.utils import get_img_colored, OfbCoreEvent

log = create_logger("OfbQtFloatingWidget")


class OfbQtFloatingWidget(QWidget):
    """
    Compact, translucent, resizable desktop widget for quick battery monitoring,
    ANC mode switching, and fast Microphone/Speaker muting.
    """

    COLOR_WHITE = (244, 246, 248, 255)
    COLOR_MUTED_GRAY = (140, 145, 155, 255)
    COLOR_WARNING = "#ffb454"
    COLOR_CRITICAL = "#ff5f57"
    COLOR_DEFAULT = "#f4f6f8"
    COLOR_DISCONNECTED = "#6b7280"

    DEFAULT_WIDTH = 290
    DEFAULT_COLLAPSED_HEIGHT = 235
    SUBMENU_HEIGHT_DELTA = 82

    MIN_WIDTH = 235
    MAX_WIDTH = 350

    MIN_COLLAPSED_HEIGHT = 195
    MAX_COLLAPSED_HEIGHT = 285

    MIN_EXPANDED_HEIGHT = 265
    MAX_EXPANDED_HEIGHT = 380

    RESIZE_MARGIN = 7

    _EDGE_LEFT = 1
    _EDGE_RIGHT = 2
    _EDGE_TOP = 4
    _EDGE_BOTTOM = 8

    def __init__(self, ctx: IOfbQtApplication):
        super().__init__(None)

        self.ctx = ctx
        self.ofb: IOpenFreebuds = ctx.ofb
        self.config = OfbQtConfigParser.get_instance()

        self._drag_start_pos: Optional[QPoint] = None
        self._resize_edges: int = 0
        self._resize_start_global: Optional[QPoint] = None
        self._resize_start_geom: Optional[QRect] = None
        self._pre_expand_height: Optional[int] = None

        self._current_anc_mode: str = "normal"
        self._anc_available: bool = False
        self._is_connected: bool = False
        self._is_expanded: bool = False
        self._eq_available: bool = False
        self._current_eq_preset: str = ""
        self._eq_preset_options: list[str] = []
        self._eq_preset_names: dict[str, str] = get_eq_preset_names()

        self._last_style_key: Optional[tuple] = None
        self._batt_font_size: int = 12
        self._batt_colors: dict[str, str] = {
            "left": "#9ca3af",
            "right": "#9ca3af",
            "case": "#9ca3af",
        }
        self._status_color: str = "#10b981"
        self._status_font_size: int = 14

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)

        self._build_ui()
        self._restore_geometry()

        # Timer for external mute changes (e.g., hardware keys, Teams/Zoom)
        self._mute_poll_timer = QTimer(self)
        self._mute_poll_timer.setInterval(1500)
        self._mute_poll_timer.timeout.connect(self._poll_mute_status)
        self._mute_poll_timer.start()

    def _build_ui(self):
        self.setMinimumSize(self.MIN_WIDTH, self.MIN_COLLAPSED_HEIGHT)
        self.setMaximumSize(self.MAX_WIDTH, self.MAX_COLLAPSED_HEIGHT)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.frame = QFrame(self)
        self.frame.setObjectName("floatingWidgetFrame")
        self.frame.setMouseTracking(True)
        main_layout.addWidget(self.frame)

        self.content_layout = QVBoxLayout(self.frame)
        self.content_layout.setContentsMargins(14, 12, 14, 13)
        self.content_layout.setSpacing(9)

        # 1. Header (Title + Status Dot + Close Button)
        self.header_layout = QHBoxLayout()
        self.header_layout.setSpacing(6)

        self.title_label = QLabel("OpenFreebuds", self.frame)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.header_layout.addWidget(self.title_label, stretch=1)

        self.status_badge = QLabel("•", self.frame)
        self.header_layout.addWidget(self.status_badge)

        self.btn_close = QPushButton("✕", self.frame)
        self.btn_close.setObjectName("btnClose")
        self.btn_close.setFixedSize(18, 18)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setToolTip(self.tr("Chiudi widget"))
        self.btn_close.clicked.connect(self.hide_widget)
        self.header_layout.addWidget(self.btn_close)

        self.content_layout.addLayout(self.header_layout)

        # 2. Battery Indicators (L, R, Case)
        self.battery_layout = QHBoxLayout()
        self.battery_layout.setSpacing(10)
        self.battery_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.batt_items = {}
        self._batt_icon_names = {"left": "batt_l", "right": "batt_r", "case": "batt_c"}
        for key in ("left", "right", "case"):
            item_box = QHBoxLayout()
            item_box.setSpacing(4)
            item_box.setAlignment(Qt.AlignmentFlag.AlignCenter)

            icon_label = QLabel(self.frame)
            icon_label.setFixedSize(16, 16)
            pix = get_img_colored(self._batt_icon_names[key], self.COLOR_WHITE, "icon/main_window", (16, 16))
            if pix is not None:
                icon_label.setPixmap(pix)

            val_label = QLabel("--", self.frame)

            item_box.addWidget(icon_label)
            item_box.addWidget(val_label)

            self.batt_items[key] = (icon_label, val_label)
            self.battery_layout.addLayout(item_box)

        self.content_layout.addLayout(self.battery_layout)

        # Divider line
        divider1 = QFrame(self.frame)
        divider1.setStyleSheet("background-color: rgba(255, 255, 255, 24); min-height: 1px; max-height: 1px;")
        self.content_layout.addWidget(divider1)

        # 3. ANC Control Section
        self.anc_header = QLabel(self.tr("CANCELLAZIONE RUMORE"), self.frame)
        self.content_layout.addWidget(self.anc_header)

        self.anc_buttons_layout = QHBoxLayout()
        self.anc_buttons_layout.setSpacing(5)

        self.anc_buttons = {}
        self._anc_icon_names = {
            "normal": "anc_off",
            "awareness": "anc_awr",
            "cancellation": "anc_on",
        }
        modes = [
            ("normal", self.tr("Off"), "anc_off"),
            ("awareness", self.tr("Trasparenza"), "anc_awr"),
            ("cancellation", self.tr("ANC"), "anc_on"),
        ]

        for mode_code, label_text, icon_name in modes:
            btn = QPushButton(self.frame)
            btn.setText(label_text)
            btn.setMinimumWidth(0)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            pix = get_img_colored(icon_name, self.COLOR_WHITE, "icon/main_window", (14, 14))
            if pix is not None:
                btn.setIcon(QIcon(pix))
            btn.setProperty("anc_active", "false")
            btn.clicked.connect(self._create_anc_callback(mode_code))
            self.anc_buttons[mode_code] = btn
            self.anc_buttons_layout.addWidget(btn)

        self.content_layout.addLayout(self.anc_buttons_layout, stretch=1)

        # Divider line
        divider2 = QFrame(self.frame)
        divider2.setStyleSheet("background-color: rgba(255, 255, 255, 24); min-height: 1px; max-height: 1px;")
        self.content_layout.addWidget(divider2)

        # 4. Quick Mute Section
        self.mute_header = QLabel(self.tr("MUTE RAPIDO"), self.frame)
        self.content_layout.addWidget(self.mute_header)

        self.mute_buttons_layout = QHBoxLayout()
        self.mute_buttons_layout.setSpacing(6)

        # Mic Mute Button
        self.btn_mic_mute = QPushButton(self.frame)
        self.btn_mic_mute.setObjectName("btnMicMute")
        self.btn_mic_mute.setMinimumWidth(0)
        self.btn_mic_mute.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.btn_mic_mute.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mic_mute.clicked.connect(self.on_toggle_mic_mute)
        self.mute_buttons_layout.addWidget(self.btn_mic_mute)

        # Speaker Mute Button
        self.btn_speaker_mute = QPushButton(self.frame)
        self.btn_speaker_mute.setObjectName("btnSpeakerMute")
        self.btn_speaker_mute.setMinimumWidth(0)
        self.btn_speaker_mute.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.btn_speaker_mute.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_speaker_mute.clicked.connect(self.on_toggle_speaker_mute)
        self.mute_buttons_layout.addWidget(self.btn_speaker_mute)

        self.content_layout.addLayout(self.mute_buttons_layout, stretch=1)

        # 5. Expand Arrow Button (toggles audio preset submenu)
        self.btn_expand = QPushButton("▾", self.frame)
        self.btn_expand.setObjectName("btnExpand")
        self.btn_expand.setFixedHeight(16)
        self.btn_expand.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_expand.setToolTip(self.tr("Mostra preset audio"))
        self.btn_expand.clicked.connect(self.toggle_submenu)
        self.content_layout.addWidget(self.btn_expand)

        # 6. Collapsible Submenu (Audio Preset)
        self.submenu_container = QWidget(self.frame)
        self.submenu_container.setObjectName("submenuContainer")
        self.submenu_layout = QVBoxLayout(self.submenu_container)
        self.submenu_layout.setContentsMargins(0, 0, 0, 0)
        self.submenu_layout.setSpacing(7)

        divider3 = QFrame(self.submenu_container)
        divider3.setStyleSheet("background-color: rgba(255, 255, 255, 24); min-height: 1px; max-height: 1px;")
        self.submenu_layout.addWidget(divider3)

        self.eq_header = QLabel(self.tr("PRESET AUDIO"), self.submenu_container)
        self.submenu_layout.addWidget(self.eq_header)

        self.btn_eq_preset = QPushButton(self.tr("--"), self.submenu_container)
        self.btn_eq_preset.setObjectName("btnEqPreset")
        self.btn_eq_preset.setMinimumWidth(0)
        self.btn_eq_preset.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.btn_eq_preset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_eq_preset.setEnabled(False)
        self.btn_eq_preset.clicked.connect(self.on_eq_preset_clicked)
        self.submenu_layout.addWidget(self.btn_eq_preset, stretch=1)

        self.eq_menu = QMenu(self)
        self.eq_menu.setObjectName("eqPresetMenu")
        self.eq_menu.setStyleSheet("""
            QMenu#eqPresetMenu {
                background-color: #1a1d24;
                color: #f4f6f8;
                border: 1px solid rgba(255, 255, 255, 42);
                border-radius: 8px;
                padding: 4px;
            }
            QMenu#eqPresetMenu::item {
                padding: 6px 14px;
                border-radius: 6px;
                font-size: 11px;
            }
            QMenu#eqPresetMenu::item:selected {
                background-color: #2563eb;
                color: #ffffff;
            }
        """)

        self.submenu_container.setVisible(False)
        self.content_layout.addWidget(self.submenu_container, stretch=1)

        self._update_mute_buttons()
        self.resize(self.DEFAULT_WIDTH, self.DEFAULT_COLLAPSED_HEIGHT)
        self._apply_responsive_scale(self.DEFAULT_WIDTH, self.DEFAULT_COLLAPSED_HEIGHT)

    # -------------------------------------------------------------
    # Responsive Scaling
    # -------------------------------------------------------------

    def _apply_responsive_scale(self, width: int, height: int):
        base_h = self.DEFAULT_COLLAPSED_HEIGHT + (self.SUBMENU_HEIGHT_DELTA if self._is_expanded else 0)
        scale_w = width / self.DEFAULT_WIDTH
        scale_h = height / max(1, base_h)
        scale = max(0.78, min(1.22, min(scale_w, scale_h)))

        mx = max(9, round(14 * scale_w))
        my_top = max(8, round(12 * scale_h))
        my_bot = max(8, round(13 * scale_h))
        sp = max(4, round(9 * scale_h))
        anc_sp = max(3, round(5 * scale_w))
        mute_sp = max(4, round(6 * scale_w))
        batt_sp = max(6, round(10 * scale_w))
        sub_sp = max(4, round(7 * scale_h))

        title_fs = max(10, min(14, round(12 * scale)))
        batt_fs = max(10, min(14, round(12 * scale)))
        header_fs = max(8, min(11, round(10 * scale)))
        btn_fs = max(9, min(13, round(11 * scale)))
        status_fs = max(11, min(16, round(14 * scale)))

        pad_v = max(2, round(5 * scale_h))
        pad_h = max(3, round(8 * scale_w))
        eq_pad_v = max(3, round(6 * scale_h))
        eq_pad_h = max(6, round(10 * scale_w))

        batt_icon_sz = max(12, min(20, round(16 * scale)))
        anc_icon_sz = max(10, min(18, round(14 * scale)))
        close_sz = max(15, min(22, round(18 * scale)))
        expand_h = max(12, min(20, round(16 * scale_h)))
        radius = max(12, min(20, round(16 * scale)))

        style_key = (
            mx, my_top, my_bot, sp, anc_sp, mute_sp, batt_sp, sub_sp,
            title_fs, batt_fs, header_fs, btn_fs, status_fs,
            pad_v, pad_h, eq_pad_v, eq_pad_h,
            batt_icon_sz, anc_icon_sz, close_sz, expand_h, radius,
        )
        if style_key == self._last_style_key:
            return
        self._last_style_key = style_key

        self.content_layout.setContentsMargins(mx, my_top, mx, my_bot)
        self.content_layout.setSpacing(sp)
        self.anc_buttons_layout.setSpacing(anc_sp)
        self.mute_buttons_layout.setSpacing(mute_sp)
        self.battery_layout.setSpacing(batt_sp)
        self.submenu_layout.setSpacing(sub_sp)

        self.btn_close.setFixedSize(close_sz, close_sz)
        self.btn_expand.setFixedHeight(expand_h)

        self._batt_font_size = batt_fs
        self._status_font_size = status_fs

        self.title_label.setStyleSheet(f"font-size: {title_fs}px; font-weight: 700; color: #f4f6f8;")
        self.status_badge.setStyleSheet(f"font-size: {status_fs}px; font-weight: bold; color: {self._status_color};")

        header_style = f"font-size: {header_fs}px; font-weight: 700; color: #8b929e; letter-spacing: 0.5px;"
        self.anc_header.setStyleSheet(header_style)
        self.mute_header.setStyleSheet(header_style)
        self.eq_header.setStyleSheet(header_style)

        for key, (icon_label, val_label) in self.batt_items.items():
            icon_label.setFixedSize(batt_icon_sz, batt_icon_sz)
            pix = get_img_colored(
                self._batt_icon_names[key],
                self.COLOR_WHITE,
                "icon/main_window",
                (batt_icon_sz, batt_icon_sz),
            )
            if pix is not None:
                icon_label.setPixmap(pix)
            val_label.setStyleSheet(
                f"font-size: {batt_fs}px; font-weight: 600; color: {self._batt_colors[key]};"
            )

        for mode_code, btn in self.anc_buttons.items():
            pix = get_img_colored(
                self._anc_icon_names[mode_code],
                self.COLOR_WHITE,
                "icon/main_window",
                (anc_icon_sz, anc_icon_sz),
            )
            if pix is not None:
                btn.setIcon(QIcon(pix))
            btn.setIconSize(QSize(anc_icon_sz, anc_icon_sz))

        close_radius = close_sz // 2
        btn_radius = max(6, round(8 * scale))
        self.frame.setStyleSheet(f"""
            #floatingWidgetFrame {{
                background-color: rgba(22, 25, 30, 238);
                border: 1px solid rgba(255, 255, 255, 42);
                border-radius: {radius}px;
            }}
            QLabel {{
                color: #f4f6f8;
                background: transparent;
            }}
            QPushButton {{
                background-color: rgba(255, 255, 255, 14);
                color: #d1d5db;
                border: 1px solid rgba(255, 255, 255, 24);
                border-radius: {btn_radius}px;
                font-size: {btn_fs}px;
                font-weight: 500;
                padding: {pad_v}px {pad_h}px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 28);
                color: #ffffff;
            }}
            QPushButton:pressed {{
                background-color: rgba(255, 255, 255, 38);
            }}
            QPushButton[anc_active="true"] {{
                background-color: #2563eb;
                color: #ffffff;
                border: 1px solid #60a5fa;
                font-weight: bold;
            }}
            QPushButton#btnMicMute[is_muted="true"] {{
                background-color: #dc2626;
                color: #ffffff;
                border: 1px solid #f87171;
                font-weight: bold;
            }}
            QPushButton#btnSpeakerMute[is_muted="true"] {{
                background-color: #d97706;
                color: #ffffff;
                border: 1px solid #fbbf24;
                font-weight: bold;
            }}
            QPushButton#btnClose {{
                background: transparent;
                border: none;
                color: #9ca3af;
                font-size: {max(11, title_fs + 1)}px;
                font-weight: bold;
                border-radius: {close_radius}px;
                padding: 0;
            }}
            QPushButton#btnClose:hover {{
                background-color: rgba(255, 255, 255, 30);
                color: #ffffff;
            }}
            QPushButton#btnExpand {{
                background: transparent;
                border: none;
                color: #8b929e;
                font-size: {btn_fs}px;
                font-weight: bold;
                border-radius: 6px;
                padding: 1px 0;
            }}
            QPushButton#btnExpand:hover {{
                background-color: rgba(255, 255, 255, 20);
                color: #ffffff;
            }}
            QPushButton#btnEqPreset {{
                text-align: center;
                padding: {eq_pad_v}px {eq_pad_h}px;
            }}
            QPushButton#btnEqPreset:disabled {{
                color: #6b7280;
                background-color: rgba(255, 255, 255, 8);
                border-color: rgba(255, 255, 255, 14);
            }}
        """)

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        self._apply_responsive_scale(event.size().width(), event.size().height())

    # -------------------------------------------------------------
    # Edge/Corner Resizing, Dragging & Geometry Persistence
    # -------------------------------------------------------------

    def _current_height_bounds(self) -> tuple[int, int]:
        if self._is_expanded:
            return self.MIN_EXPANDED_HEIGHT, self.MAX_EXPANDED_HEIGHT
        return self.MIN_COLLAPSED_HEIGHT, self.MAX_COLLAPSED_HEIGHT

    def _hit_test_edges(self, pos: QPoint) -> int:
        edges = 0
        m = self.RESIZE_MARGIN
        if pos.x() <= m:
            edges |= self._EDGE_LEFT
        elif pos.x() >= self.width() - m:
            edges |= self._EDGE_RIGHT
        if pos.y() <= m:
            edges |= self._EDGE_TOP
        elif pos.y() >= self.height() - m:
            edges |= self._EDGE_BOTTOM
        return edges

    def _update_cursor_for_pos(self, pos: QPoint):
        edges = self._hit_test_edges(pos)
        if edges in (self._EDGE_LEFT | self._EDGE_TOP, self._EDGE_RIGHT | self._EDGE_BOTTOM):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif edges in (self._EDGE_RIGHT | self._EDGE_TOP, self._EDGE_LEFT | self._EDGE_BOTTOM):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif edges in (self._EDGE_LEFT, self._EDGE_RIGHT):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif edges in (self._EDGE_TOP, self._EDGE_BOTTOM):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()

    def event(self, e: QEvent) -> bool:
        if e.type() == QEvent.Type.HoverMove and self._resize_edges == 0 and self._drag_start_pos is None:
            self._update_cursor_for_pos(e.position().toPoint())
        elif e.type() == QEvent.Type.HoverLeave and self._resize_edges == 0:
            self.unsetCursor()
        return super().event(e)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            edges = self._hit_test_edges(pos)
            if edges != 0:
                self._resize_edges = edges
                self._resize_start_global = event.globalPosition().toPoint()
                self._resize_start_geom = self.geometry()
            else:
                self._drag_start_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._resize_edges != 0 and self._resize_start_geom is not None and self._resize_start_global is not None:
            delta = event.globalPosition().toPoint() - self._resize_start_global
            geom = self._resize_start_geom
            min_h, max_h = self._current_height_bounds()

            new_x = geom.x()
            new_y = geom.y()
            new_w = geom.width()
            new_h = geom.height()

            if self._resize_edges & self._EDGE_LEFT:
                new_w = max(self.MIN_WIDTH, min(self.MAX_WIDTH, geom.width() - delta.x()))
                new_x = geom.right() + 1 - new_w
            elif self._resize_edges & self._EDGE_RIGHT:
                new_w = max(self.MIN_WIDTH, min(self.MAX_WIDTH, geom.width() + delta.x()))

            if self._resize_edges & self._EDGE_TOP:
                new_h = max(min_h, min(max_h, geom.height() - delta.y()))
                new_y = geom.bottom() + 1 - new_h
            elif self._resize_edges & self._EDGE_BOTTOM:
                new_h = max(min_h, min(max_h, geom.height() + delta.y()))

            self.setGeometry(new_x, new_y, new_w, new_h)
            event.accept()
            return

        if self._drag_start_pos is not None and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_start_pos)
            event.accept()
            return

        self._update_cursor_for_pos(event.position().toPoint())

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._resize_edges != 0:
            self._resize_edges = 0
            self._resize_start_global = None
            self._resize_start_geom = None
            self._pre_expand_height = None
            self._save_geometry()
            event.accept()
            return

        if self._drag_start_pos is not None:
            self._drag_start_pos = None
            self._save_geometry()
            event.accept()

    def _save_geometry(self):
        self.config.set("floating_widget", "pos_x", self.x())
        self.config.set("floating_widget", "pos_y", self.y())
        self.config.set("floating_widget", "width", self.width())
        collapsed_h = self.height()
        if self._is_expanded:
            scale_h = self.height() / (self.DEFAULT_COLLAPSED_HEIGHT + self.SUBMENU_HEIGHT_DELTA)
            collapsed_h = max(
                self.MIN_COLLAPSED_HEIGHT,
                min(self.MAX_COLLAPSED_HEIGHT, self.height() - round(self.SUBMENU_HEIGHT_DELTA * scale_h)),
            )
        self.config.set("floating_widget", "height", collapsed_h)
        self.config.save()

    def _restore_geometry(self):
        saved_w = self.config.get("floating_widget", "width", self.DEFAULT_WIDTH)
        saved_h = self.config.get("floating_widget", "height", self.DEFAULT_COLLAPSED_HEIGHT)
        if not isinstance(saved_w, int):
            saved_w = self.DEFAULT_WIDTH
        if not isinstance(saved_h, int):
            saved_h = self.DEFAULT_COLLAPSED_HEIGHT

        w = max(self.MIN_WIDTH, min(self.MAX_WIDTH, saved_w))
        h = max(self.MIN_COLLAPSED_HEIGHT, min(self.MAX_COLLAPSED_HEIGHT, saved_h))
        self.resize(w, h)
        self._apply_responsive_scale(w, h)

        screen = QGuiApplication.primaryScreen()
        geometry = screen.availableGeometry()

        default_x = geometry.x() + geometry.width() - w - 28
        default_y = geometry.y() + geometry.height() - h - 48

        saved_x = self.config.get("floating_widget", "pos_x", default_x)
        saved_y = self.config.get("floating_widget", "pos_y", default_y)

        # Verify on-screen bounds
        if saved_x < geometry.x() or saved_x > geometry.right() - 50:
            saved_x = default_x
        if saved_y < geometry.y() or saved_y > geometry.bottom() - 50:
            saved_y = default_y

        self.move(saved_x, saved_y)

    # -------------------------------------------------------------
    # Visibility and Toggle
    # -------------------------------------------------------------

    def show_widget(self):
        self.show()
        self.raise_()
        self._apply_responsive_scale(self.width(), self.height())
        self.config.set("floating_widget", "enabled", True)
        self.config.save()
        if getattr(self.ctx, "tray", None) and getattr(self.ctx.tray, "menu", None):
            self.ctx.tray.menu.floating_widget_action.setChecked(True)
        asyncio.create_task(self.ofb.send_message(OfbEventKind.QT_SETTINGS_CHANGED))
        asyncio.create_task(self.update_ui())

    def hide_widget(self):
        self.hide()
        self.config.set("floating_widget", "enabled", False)
        self.config.save()
        if getattr(self.ctx, "tray", None) and getattr(self.ctx.tray, "menu", None):
            self.ctx.tray.menu.floating_widget_action.setChecked(False)
        asyncio.create_task(self.ofb.send_message(OfbEventKind.QT_SETTINGS_CHANGED))

    def toggle_visibility(self):
        if self.isVisible():
            self.hide_widget()
        else:
            self.show_widget()

    # -------------------------------------------------------------
    # Mute Handlers
    # -------------------------------------------------------------

    def _poll_mute_status(self):
        if self.isVisible():
            self._update_mute_buttons()

    def _update_mute_buttons(self):
        is_mic_muted = openfreebuds_backend.get_microphone_mute()
        is_speaker_muted = openfreebuds_backend.get_speaker_mute()

        # Update Mic button
        if is_mic_muted:
            self.btn_mic_mute.setText(self.tr("🚫 Mic Muto"))
            self.btn_mic_mute.setProperty("is_muted", "true")
            self.btn_mic_mute.setToolTip(self.tr("Microfono disattivato (clicca per riattivare)"))
        else:
            self.btn_mic_mute.setText(self.tr("🎙️ Mic Attivo"))
            self.btn_mic_mute.setProperty("is_muted", "false")
            self.btn_mic_mute.setToolTip(self.tr("Microfono attivo (clicca per silenziare)"))

        # Update Speaker button
        if is_speaker_muted:
            self.btn_speaker_mute.setText(self.tr("🔇 Audio Muto"))
            self.btn_speaker_mute.setProperty("is_muted", "true")
            self.btn_speaker_mute.setToolTip(self.tr("Audio di sistema disattivato (clicca per riattivare)"))
        else:
            self.btn_speaker_mute.setText(self.tr("🔊 Audio ON"))
            self.btn_speaker_mute.setProperty("is_muted", "false")
            self.btn_speaker_mute.setToolTip(self.tr("Audio attivo (clicca per silenziare)"))

        self.btn_mic_mute.style().unpolish(self.btn_mic_mute)
        self.btn_mic_mute.style().polish(self.btn_mic_mute)
        self.btn_speaker_mute.style().unpolish(self.btn_speaker_mute)
        self.btn_speaker_mute.style().polish(self.btn_speaker_mute)

    @pyqtSlot()
    def on_toggle_mic_mute(self):
        openfreebuds_backend.toggle_microphone_mute()
        self._update_mute_buttons()

    @pyqtSlot()
    def on_toggle_speaker_mute(self):
        openfreebuds_backend.toggle_speaker_mute()
        self._update_mute_buttons()

    # -------------------------------------------------------------
    # ANC Click Handler
    # -------------------------------------------------------------

    def _create_anc_callback(self, mode: str):
        def _on_click():
            asyncio.create_task(self.on_anc_clicked(mode))
        return _on_click

    async def on_anc_clicked(self, mode: str):
        if not self._is_connected or not self._anc_available:
            return

        try:
            log.info(f"Setting ANC mode to {mode}")
            await self.ofb.set_property("anc", "mode", mode)
            self._current_anc_mode = mode
            self._highlight_anc_button(mode)
        except Exception:
            log.exception(f"Failed to set ANC mode to {mode}")

    def _highlight_anc_button(self, active_mode: str):
        for code, btn in self.anc_buttons.items():
            is_active = (code == active_mode)
            btn.setProperty("anc_active", "true" if is_active else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # -------------------------------------------------------------
    # UI Core Update Loop
    # -------------------------------------------------------------

    async def update_ui(self, event: Optional[OfbCoreEvent] = None):
        state = await self.ofb.get_state()
        self._is_connected = (state == IOpenFreebuds.STATE_CONNECTED)

        device_name, _ = await self.ofb.get_device_tags()
        if self._is_connected and device_name:
            self.title_label.setText(device_name)
            self._status_color = "#10b981"
            self.status_badge.setStyleSheet(
                f"font-size: {self._status_font_size}px; font-weight: bold; color: {self._status_color};"
            )
            self.status_badge.setToolTip(self.tr("Dispositivo connesso"))
        else:
            self.title_label.setText(self.tr("Disconnesso"))
            self._status_color = self.COLOR_DISCONNECTED
            self.status_badge.setStyleSheet(
                f"font-size: {self._status_font_size}px; font-weight: bold; color: {self._status_color};"
            )
            self.status_badge.setToolTip(self.tr("Nessun dispositivo connesso"))

        # Battery update
        battery = await self.ofb.get_property("battery") or {}
        for key in ("left", "right", "case"):
            _, val_label = self.batt_items[key]
            if self._is_connected and key in battery and isinstance(battery[key], int):
                val = battery[key]
                val_label.setText(f"{val}%")
                if val <= 15:
                    val_color = self.COLOR_CRITICAL
                elif val <= 25:
                    val_color = self.COLOR_WARNING
                else:
                    val_color = self.COLOR_DEFAULT
            else:
                val_label.setText("--")
                val_color = self.COLOR_DISCONNECTED
            self._batt_colors[key] = val_color
            val_label.setStyleSheet(
                f"font-size: {self._batt_font_size}px; font-weight: 600; color: {val_color};"
            )

        # ANC update
        anc_data = await self.ofb.get_property("anc") or {}
        self._anc_available = bool(self._is_connected and "mode" in anc_data)
        current_mode = anc_data.get("mode", "normal")
        self._current_anc_mode = current_mode

        for code, btn in self.anc_buttons.items():
            btn.setEnabled(self._anc_available)

        self._highlight_anc_button(current_mode if self._anc_available else "")

        # Audio Preset (EQ) update
        sound_data = await self.ofb.get_property("sound") or {}
        eq_preset = sound_data.get("equalizer_preset")
        eq_options_raw = sound_data.get("equalizer_preset_options")
        self._eq_available = bool(self._is_connected and eq_preset is not None and eq_options_raw)
        if self._eq_available:
            self._current_eq_preset = eq_preset
            self._eq_preset_options = [opt for opt in eq_options_raw.split(",") if opt]
        else:
            self._current_eq_preset = ""
            self._eq_preset_options = []

        self._update_eq_preset_button()

        # Mute update
        self._update_mute_buttons()

    # -------------------------------------------------------------
    # Collapsible Submenu & Audio Preset Handlers
    # -------------------------------------------------------------

    @pyqtSlot()
    def toggle_submenu(self):
        if not self._is_expanded:
            self._pre_expand_height = self.height()
            scale_h = self.height() / self.DEFAULT_COLLAPSED_HEIGHT
            delta = round(self.SUBMENU_HEIGHT_DELTA * scale_h)
            target_h = self.height() + delta
        else:
            if self._pre_expand_height is not None:
                target_h = self._pre_expand_height
                self._pre_expand_height = None
            else:
                scale_h = self.height() / (self.DEFAULT_COLLAPSED_HEIGHT + self.SUBMENU_HEIGHT_DELTA)
                delta = round(self.SUBMENU_HEIGHT_DELTA * scale_h)
                target_h = self.height() - delta

        self._is_expanded = not self._is_expanded
        self.submenu_container.setVisible(self._is_expanded)
        self.btn_expand.setText("▴" if self._is_expanded else "▾")
        self.btn_expand.setToolTip(
            self.tr("Nascondi preset audio") if self._is_expanded else self.tr("Mostra preset audio")
        )

        min_h, max_h = self._current_height_bounds()
        self.setMinimumSize(self.MIN_WIDTH, min_h)
        self.setMaximumSize(self.MAX_WIDTH, max_h)

        new_h = max(min_h, min(max_h, target_h))

        self._last_style_key = None  # Force rescale for new base height
        self.resize(self.width(), new_h)
        self._apply_responsive_scale(self.width(), new_h)

    def _get_eq_display_name(self, code: str) -> str:
        return self._eq_preset_names.get(code, code)

    def _update_eq_preset_button(self):
        self.btn_eq_preset.setEnabled(self._eq_available)
        if self._eq_available and self._current_eq_preset:
            display = self._get_eq_display_name(self._current_eq_preset)
            self.btn_eq_preset.setText(f"🎵 {display}  ▾")
            self.btn_eq_preset.setToolTip(self.tr("Clicca per cambiare preset audio"))
        else:
            self.btn_eq_preset.setText(self.tr("Non disponibile"))
            self.btn_eq_preset.setToolTip(self.tr("Preset audio non disponibile"))

    def _populate_eq_menu(self):
        self.eq_menu.clear()
        for code in self._eq_preset_options:
            action = self.eq_menu.addAction(self._get_eq_display_name(code))
            action.setCheckable(True)
            action.setChecked(code == self._current_eq_preset)
            action.triggered.connect(self._create_eq_callback(code))

    def _create_eq_callback(self, code: str):
        def _on_select():
            asyncio.create_task(self.on_eq_preset_selected(code))
        return _on_select

    @pyqtSlot()
    def on_eq_preset_clicked(self):
        if not self._eq_available or not self._eq_preset_options:
            return
        self._populate_eq_menu()
        self.eq_menu.setMinimumWidth(self.btn_eq_preset.width())
        pos = self.btn_eq_preset.mapToGlobal(QPoint(0, self.btn_eq_preset.height() + 4))
        self.eq_menu.exec(pos)

    async def on_eq_preset_selected(self, code: str):
        if not self._eq_available:
            return
        try:
            log.info(f"Setting equalizer preset to {code}")
            await self.ofb.set_property("sound", "equalizer_preset", code)
            self._current_eq_preset = code
            self._update_eq_preset_button()
        except Exception:
            log.exception(f"Failed to set equalizer preset to {code}")
