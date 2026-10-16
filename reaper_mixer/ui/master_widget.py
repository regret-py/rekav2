"""
Panneau master : titre + engrenage (réglages), badge de connexion, knob du
casque (bus A1) avec son interpolation douce, VU-mètre master.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QSizePolicy,
                               QVBoxLayout, QWidget)

from ui import styles
from ui.fader import format_db
from ui.knob import Knob
from ui.strip_widget import make_section
from ui.vu_meter import VuMeter

MASTER_WIDTH = 138
HEADPHONE_KNOB = 72
PULSE_MS = 1500


class GearButton(QAbstractButton):
    """Bouton carré avec une icône d'engrenage dessinée."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Réglages")

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hover = self.underMouse()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(styles.BUTTON_HOVER if hover else styles.BUTTON_BG))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 7, 7)

        c = QPointF(self.width() / 2, self.height() / 2)
        outer, inner, teeth = 7.0, 5.2, 8
        path = QPainterPath()
        for i in range(teeth * 2):
            # Alternance dent / creux, chaque segment couvrant 1/16 de tour
            radius = outer if i % 2 == 0 else inner
            for k in (0.0, 1.0):
                a = 2 * math.pi * (i + k * 0.999) / (teeth * 2)
                point = QPointF(c.x() + radius * math.cos(a), c.y() + radius * math.sin(a))
                if i == 0 and k == 0.0:
                    path.moveTo(point)
                else:
                    path.lineTo(point)
        path.closeSubpath()
        hole = QPainterPath()
        hole.addEllipse(c, 2.3, 2.3)
        ink = QColor(styles.TEXT_PRIMARY if hover else styles.TEXT_SECONDARY)
        p.setPen(QPen(ink, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.drawPath(hole)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)


class MinimizeButton(QAbstractButton):
    """Bouton carré « – » : réduit REKA (dans la barre des tâches ou la zone
    de notification, selon les réglages)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Réduire REKA")

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hover = self.underMouse()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(styles.BUTTON_HOVER if hover else styles.BUTTON_BG))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 7, 7)
        p.setPen(QPen(QColor(styles.TEXT_PRIMARY if hover else styles.TEXT_SECONDARY), 1.8,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        y = self.height() / 2 + 3
        p.drawLine(QPointF(8, y), QPointF(self.width() - 8, y))

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)


class ConnectionBadge(QWidget):
    """Pill « REAPER connecté » avec un point qui pulse (opacité 0,5 → 1 en 1,5 s)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ok = False
        self._text = "Connexion…"
        self._opacity = 1.0
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._pulse = QVariantAnimation(self)
        self._pulse.setDuration(PULSE_MS)
        self._pulse.setStartValue(0.5)
        self._pulse.setKeyValueAt(0.5, 1.0)
        self._pulse.setEndValue(0.5)
        self._pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pulse.setLoopCount(-1)
        self._pulse.valueChanged.connect(self._on_pulse)

    def set_status(self, ok: bool, text: str, detail: str = "") -> None:
        self._ok, self._text = ok, text
        self.setToolTip(detail or text)
        if ok:
            self._pulse.start()
        else:
            self._pulse.stop()
            self._opacity = 1.0
        self.update()

    def _on_pulse(self, value: float) -> None:
        self._opacity = float(value)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QPen(QColor(styles.BORDER_COLUMN), 1))
        p.setBrush(QColor("#030b06"))
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)

        dot = QColor(styles.GREEN_NEON if self._ok else styles.RED_MUTE)
        center = QPointF(12, rect.center().y())
        p.setPen(Qt.PenStyle.NoPen)
        if self._ok:
            p.setBrush(styles.color(styles.GREEN_NEON, 0.25 * self._opacity))
            p.drawEllipse(center, 6, 6)
        dot.setAlphaF(self._opacity)
        p.setBrush(dot)
        p.drawEllipse(center, 3.5, 3.5)

        p.setFont(styles.font(10, QFont.Weight.DemiBold))
        p.setPen(QColor(styles.TEXT_SECONDARY if self._ok else styles.TEXT_PRIMARY))
        text_rect = rect.adjusted(22, 0, -6, 0)
        text = p.fontMetrics().elidedText(self._text, Qt.TextElideMode.ElideRight,
                                          int(text_rect.width()))
        p.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)


class ProjectButton(QAbstractButton):
    """Pill « Projet : <nom> » : ouvre le choix du projet REAPER."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = "…"
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_project(self, name: str, path: str) -> None:
        self._name = name
        self.setToolTip(f"Projet REAPER ouvert :\n{path}\n\nClic : changer de projet")
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        hover = self.underMouse()
        p.setPen(QPen(QColor(styles.GREEN_DIM if hover else styles.BORDER_COLUMN), 1))
        p.setBrush(QColor(styles.BUTTON_HOVER if hover else "#030b06"))
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        # Petite icône de dossier
        p.setPen(QPen(QColor(styles.TEXT_SECONDARY), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        folder = QRectF(8, rect.center().y() - 3.5, 9, 7)
        p.drawRoundedRect(folder, 1.2, 1.2)
        p.drawLine(QPointF(folder.left() + 1, folder.top()), QPointF(folder.left() + 3.5, folder.top() - 1.5))
        p.setFont(styles.font(10, QFont.Weight.DemiBold))
        p.setPen(QColor(styles.TEXT_PRIMARY if hover else styles.TEXT_SECONDARY))
        text_rect = rect.adjusted(22, 0, -8, 0)
        text = p.fontMetrics().elidedText(self._name, Qt.TextElideMode.ElideRight,
                                          int(text_rect.width()))
        p.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)


class MasterWidget(QFrame):
    settingsRequested = Signal()
    projectRequested = Signal()
    minimizeRequested = Signal()
    headphoneChanged = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("column")
        self.setFixedWidth(MASTER_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(2, 0, 0, 0)
        header.setSpacing(4)
        title = QLabel("Master")
        title.setObjectName("masterTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.minimize = MinimizeButton()
        self.minimize.clicked.connect(self.minimizeRequested)
        header.addWidget(self.minimize)
        self.gear = GearButton()
        self.gear.clicked.connect(self.settingsRequested)
        header.addWidget(self.gear)
        layout.addLayout(header)

        self.badge = ConnectionBadge()
        layout.addWidget(self.badge)
        self.project = ProjectButton()
        self.project.clicked.connect(self.projectRequested)
        layout.addWidget(self.project)

        knob_section = make_section()
        knob_box = QVBoxLayout(knob_section)
        knob_box.setContentsMargins(6, 10, 6, 10)
        knob_box.setSpacing(2)
        self.knob = Knob(diameter=HEADPHONE_KNOB, checkable=False, smooth=True)
        self.knob.setToolTip("Volume du casque — double-clic : 0 dB")
        self.knob.valueChanged.connect(self._on_knob)
        knob_box.addWidget(self.knob, 0, Qt.AlignmentFlag.AlignHCenter)
        self.value_label = QLabel()
        self.value_label.setObjectName("masterValue")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        knob_box.addWidget(self.value_label)
        caption = QLabel("Casque")
        caption.setObjectName("masterLabel")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        knob_box.addWidget(caption)
        layout.addWidget(knob_section)

        vu_section = make_section()
        vu_box = QHBoxLayout(vu_section)
        vu_box.setContentsMargins(0, 12, 0, 12)
        vu_box.addStretch(1)
        self.vu = VuMeter(channels=2, bar_width=5, gap=26, margin_top=4, margin_bottom=4,
                          clip_leds=True)
        vu_box.addWidget(self.vu)
        vu_box.addStretch(1)
        layout.addWidget(vu_section, 1)

        self._show_value(0.0)

    def set_headphone(self, db: float) -> None:
        self.knob.setValue(db)
        if not self.knob.isInteracting():
            self._show_value(db)

    def set_levels(self, levels: tuple[float, float]) -> None:
        self.vu.set_levels(levels)

    def set_status(self, ok: bool, text: str, detail: str = "") -> None:
        self.badge.set_status(ok, text, detail)

    def set_project(self, name: str, path: str) -> None:
        self.project.set_project(name, path)

    def _on_knob(self, db: float) -> None:
        self._show_value(db)
        self.headphoneChanged.emit(db)

    def _show_value(self, db: float) -> None:
        self.value_label.setText(f"{format_db(db)} dB")
