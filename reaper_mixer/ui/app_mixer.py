"""
Fenêtre « Applications » d'une strip virtuelle : une ligne par application qui
joue sur l'entrée Voicemeeter de la strip, avec son volume, son niveau en
direct et son mute (les réglages Windows de l'application, comme le mélangeur
de volume de Windows).

S'ouvre sous le bouton « Apps » de la strip, se ferme en cliquant ailleurs.
"""

from __future__ import annotations

from PySide6.QtCore import QFileInfo, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QApplication, QFileIconProvider, QFrame,
                               QHBoxLayout, QLabel, QSizePolicy, QSlider, QStyle, QVBoxLayout,
                               QWidget)

from core.app_volume import SYSTEM_KEY, AppInfo, AppVolumeBackend
from ui import styles

POPUP_WIDTH = 340
REFRESH_MS = 150             # niveaux et liste des applications
ICON_SIZE = 18
NAME_WIDTH = 112

_icons: dict[str, QIcon] = {}


def letter_icon(name: str) -> QIcon:
    """Pastille avec l'initiale de l'application (quand elle n'a pas d'icône)."""
    size = ICON_SIZE * 2
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(styles.GREEN_DIM))
    p.drawEllipse(QRectF(0, 0, size, size))
    p.setPen(QColor(styles.TEXT_PRIMARY))
    p.setFont(styles.font(int(size * 0.5), QFont.Weight.DemiBold))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, (name or "?")[:1].upper())
    p.end()
    return QIcon(pixmap)


def app_icon(app: AppInfo) -> QIcon:
    """Icône de l'exécutable (mise en cache), sinon initiale de l'application."""
    key = app.exe or app.key
    icon = _icons.get(key)
    if icon is None:
        icon = QIcon()
        if app.exe:
            icon = QFileIconProvider().icon(QFileInfo(app.exe))
        if icon.isNull() and app.key == SYSTEM_KEY:
            icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume)
        if icon.isNull() or not icon.availableSizes() and icon.pixmap(ICON_SIZE).isNull():
            icon = letter_icon(app.name)
        _icons[key] = icon
    return icon


class PeakBar(QWidget):
    """Fine barre de niveau sous le curseur de volume."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._level = 0.0
        self.setFixedHeight(4)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_level(self, level: float) -> None:
        level = max(0.0, min(1.0, level))
        if abs(level - self._level) > 0.004:
            self._level = level
            self.update()

    def level(self) -> float:
        return self._level

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(styles.color(styles.GREEN_DIM, 0.35))
        p.drawRoundedRect(rect, 2, 2)
        if self._level > 0.004:
            p.setBrush(QColor(styles.RED_MUTE if self._level > 0.98 else styles.GREEN_NEON))
            p.drawRoundedRect(QRectF(0, 0, rect.width() * self._level, rect.height()), 2, 2)


class SpeakerButton(QAbstractButton):
    """Haut-parleur (clic = mute) : rouge et barré quand l'application est coupée."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(28, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(lambda on: self.setToolTip("Réactiver le son" if on else "Couper le son"))
        self.setToolTip("Couper le son")

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        muted = self.isChecked()
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(Qt.PenStyle.NoPen)
        if muted:
            p.setBrush(QColor(styles.RED_MUTE))
            p.drawRoundedRect(rect, 6, 6)
        elif self.underMouse():
            p.setBrush(QColor(styles.BUTTON_HOVER))
            p.drawRoundedRect(rect, 6, 6)
        ink = QColor(styles.TEXT_ON_RED if muted else styles.TEXT_PRIMARY)
        cx, cy = self.width() / 2 - 3, self.height() / 2
        body = QPainterPath()
        body.moveTo(cx - 6, cy - 2.5)
        body.lineTo(cx - 3, cy - 2.5)
        body.lineTo(cx + 1, cy - 6)
        body.lineTo(cx + 1, cy + 6)
        body.lineTo(cx - 3, cy + 2.5)
        body.lineTo(cx - 6, cy + 2.5)
        body.closeSubpath()
        p.setBrush(ink)
        p.drawPath(body)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(ink, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if muted:
            p.drawLine(QPointF(cx + 4, cy - 3), QPointF(cx + 9, cy + 3))
            p.drawLine(QPointF(cx + 9, cy - 3), QPointF(cx + 4, cy + 3))
        else:
            p.drawArc(QRectF(cx - 1, cy - 4, 7, 8), -50 * 16, 100 * 16)
            p.drawArc(QRectF(cx - 2, cy - 7, 12, 14), -50 * 16, 100 * 16)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)


class AppRow(QWidget):
    volumeChanged = Signal(str, float)
    muteToggled = Signal(str, bool)

    def __init__(self, app: AppInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = app.key
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)

        self.icon = QLabel()
        self.icon.setFixedSize(ICON_SIZE, ICON_SIZE)
        self.icon.setPixmap(app_icon(app).pixmap(QSize(ICON_SIZE, ICON_SIZE)))
        layout.addWidget(self.icon)

        self.name = QLabel()
        self.name.setObjectName("appName")
        self.name.setFixedWidth(NAME_WIDTH)
        self.name.setText(self.name.fontMetrics().elidedText(app.name, Qt.TextElideMode.ElideRight,
                                                             NAME_WIDTH))
        self.name.setToolTip(app.exe or app.name)
        layout.addWidget(self.name)

        column = QVBoxLayout()
        column.setSpacing(2)
        column.setContentsMargins(0, 3, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.valueChanged.connect(self._on_slider)
        column.addWidget(self.slider)
        self.peak = PeakBar()
        column.addWidget(self.peak)
        layout.addLayout(column, 1)

        self.percent = QLabel()
        self.percent.setObjectName("appPercent")
        self.percent.setFixedWidth(34)
        self.percent.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.percent)

        self.mute = SpeakerButton()
        self.mute.toggled.connect(lambda on: self.muteToggled.emit(self.key, on))
        layout.addWidget(self.mute)
        self.update_from(app)

    def update_from(self, app: AppInfo) -> None:
        value = round(app.volume * 100)
        if not self.slider.isSliderDown() and self.slider.value() != value:
            self.slider.blockSignals(True)
            self.slider.setValue(value)
            self.slider.blockSignals(False)
        self.percent.setText(f"{self.slider.value()} %")
        if self.mute.isChecked() != app.muted:
            self.mute.blockSignals(True)
            self.mute.setChecked(app.muted)
            self.mute.blockSignals(False)
            self.mute.update()
        self.peak.set_level(0.0 if app.muted else app.peak)
        self.name.setEnabled(app.active or app.key == SYSTEM_KEY)

    def _on_slider(self, value: int) -> None:
        self.percent.setText(f"{value} %")
        self.volumeChanged.emit(self.key, value / 100)


class AppMixerPopup(QFrame):
    """Fenêtre volante sous le bouton « Apps » d'une strip."""

    def __init__(self, backend: AppVolumeBackend, index: int, strip_name: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("appMixer")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)   # coins arrondis
        self.setFixedWidth(POPUP_WIDTH)
        self._backend = backend
        self._index = index
        self.rows: dict[str, AppRow] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        title = QLabel(f"Applications — {strip_name}")
        title.setObjectName("appMixerTitle")
        layout.addWidget(title)
        self.device = QLabel()
        self.device.setObjectName("appMixerHint")
        self.device.setWordWrap(True)
        layout.addWidget(self.device)

        self.list = QVBoxLayout()
        self.list.setSpacing(2)
        layout.addLayout(self.list)
        self.empty = QLabel()
        self.empty.setObjectName("appMixerHint")
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)

        hint = QLabel("Volume Windows de chaque application, comme dans le mélangeur de volume.")
        hint.setObjectName("appMixerHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self.refresh()
        self._timer.start()

    def refresh(self) -> None:
        device = self._backend.device_name(self._index)
        self.device.setText(f"Sortie Windows : {device}" if device
                            else "Entrée de Voicemeeter introuvable dans Windows.")
        apps = self._backend.apps(self._index) if device else []
        keys = [app.key for app in apps]
        if keys != list(self.rows):
            self._rebuild(apps)
        else:
            for app in apps:
                self.rows[app.key].update_from(app)
        self.empty.setVisible(not apps)
        if not apps:
            self.empty.setText(
                "Aucune application ne joue ici pour l'instant. Choisis cette sortie "
                "dans l'application, ou dans Windows : Paramètres › Son › Mélangeur de volume."
                if device else
                "Vérifie que Voicemeeter est installé et lancé.")

    def _rebuild(self, apps: list[AppInfo]) -> None:
        for row in self.rows.values():
            self.list.removeWidget(row)
            row.deleteLater()
        self.rows = {}
        for app in apps:
            row = AppRow(app, self)
            row.volumeChanged.connect(lambda key, v: self._backend.set_volume(self._index, key, v))
            row.muteToggled.connect(lambda key, on: self._backend.set_mute(self._index, key, on))
            self.list.addWidget(row)
            self.rows[app.key] = row
        self.adjustSize()

    def show_under(self, anchor: QWidget) -> None:
        """Ouvre la fenêtre sous `anchor` (au-dessus si l'écran manque de place)."""
        self.adjustSize()
        below = anchor.mapToGlobal(QPoint(0, anchor.height() + 4))
        screen = anchor.screen() or QApplication.primaryScreen()
        x, y = below.x() + anchor.width() // 2 - self.width() // 2, below.y()
        if screen is not None:
            area = screen.availableGeometry()
            if y + self.height() > area.bottom():
                y = anchor.mapToGlobal(QPoint(0, 0)).y() - self.height() - 4
            x = max(area.left() + 4, min(x, area.right() - self.width() - 4))
            y = max(area.top() + 4, y)
        self.move(x, y)
        self.show()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().closeEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(styles.GREEN_DIM), 1))
        p.setBrush(QColor(styles.BG_COLUMN))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 10, 10)

