"""
Knob circulaire custom (paintEvent, antialiasé).

- Strips : clic = routing on/off vers le bus, glisser verticalement = niveau
  d'envoi vers ce bus (Maj = réglage fin), molette = idem.
- Master (casque) : pas de bascule ; le dessin suit la valeur avec une
  interpolation douce, y compris pendant le glisser.

Course de 270° (vide en bas), même loi dB que les faders : 0 dB = butée haute.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui import styles
from ui.fader import FADER_MAX_DB, FADER_MIN_DB, db_to_ratio, format_db, ratio_to_db

START_ANGLE = 225.0      # degrés, sens trigo (225° = en bas à gauche)
SPAN = 270.0
DRAG_PX = 160.0          # pixels de glisser pour toute la course
DRAG_FINE = 0.25
CLICK_SLOP = 3.0         # au-delà, un appui devient un glisser (et plus un clic)
WHEEL_STEP = 0.025
SMOOTH_MS = 16
SMOOTH_FACTOR = 0.28


def _position(db: float) -> float:
    """dB → position sur la course (0 = -60 dB, 1 = 0 dB)."""
    return 1.0 - db_to_ratio(db)


class Knob(QWidget):
    valueChanged = Signal(float)
    toggled = Signal(bool)

    def __init__(self, diameter: int = 30, checkable: bool = True, smooth: bool = False,
                 default_db: float = 0.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._diameter = diameter
        self._checkable = checkable
        self._smooth = smooth
        self._default = default_db
        self._value = default_db
        self._active = True
        self._shown = _position(default_db)   # position dessinée (interpolée si smooth)
        self._dragging = False
        self._moved = False
        self._press_y = 0.0
        self._press_pos = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(SMOOTH_MS)
        self._timer.timeout.connect(self._animate)
        size = diameter + 8
        self.setFixedSize(size, size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_tooltip()

    # ------------------------------------------------------------------ API
    def value(self) -> float:
        return self._value

    def setValue(self, db: float) -> None:  # noqa: N802
        """Valeur venant de Voicemeeter ; ignorée pendant un glisser."""
        if self._dragging or abs(db - self._value) < 0.005:
            return
        self._value = db
        self._retarget()

    def isActive(self) -> bool:  # noqa: N802
        return self._active

    def setActive(self, active: bool) -> None:  # noqa: N802
        if active != self._active:
            self._active = active
            self._update_tooltip()
            self.update()

    def isInteracting(self) -> bool:  # noqa: N802
        return self._dragging

    # -------------------------------------------------------------- dessin
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = float(self._diameter)
        ring = max(2.6, d * 0.12)
        center = QPointF(self.width() / 2, self.height() / 2)
        arc_rect = QRectF(center.x() - (d - ring) / 2, center.y() - (d - ring) / 2,
                          d - ring, d - ring)
        pos = max(0.0, min(1.0, self._shown))
        span_value = -SPAN * pos
        arc = int(START_ANGLE * 16)

        if self._active:
            # Halo, piste, puis arc de valeur
            p.setPen(QPen(styles.color(styles.GREEN_NEON, 0.16), ring * 2.0,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(arc_rect, arc, int(span_value * 16))
            p.setPen(QPen(styles.color(styles.GREEN_DIM, 0.7), ring,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(arc_rect, arc, int(-SPAN * 16))
            if pos > 0.002:
                p.setPen(QPen(QColor(styles.GREEN_NEON), ring,
                              Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawArc(arc_rect, arc, int(span_value * 16))
        else:
            # Éteint : piste à peine visible + point vert au départ de la course
            p.setPen(QPen(styles.color(styles.GREEN_DIM, 0.25), ring * 0.6,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(arc_rect, arc, int(-SPAN * 16))
            start = math.radians(START_ANGLE)
            r = arc_rect.width() / 2
            dot = QPointF(center.x() + r * math.cos(start), center.y() - r * math.sin(start))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(styles.color(styles.GREEN_NEON, 0.85))
            p.drawEllipse(dot, ring * 0.45, ring * 0.45)

        # Bouton central
        cap_r = d / 2 - ring - max(2.0, ring * 0.55)
        gradient = QRadialGradient(center.x() - cap_r * 0.3, center.y() - cap_r * 0.4, cap_r * 1.6)
        gradient.setColorAt(0, QColor(styles.KNOB_CAP_TOP))
        gradient.setColorAt(1, QColor(styles.KNOB_CAP_BOTTOM))
        p.setBrush(gradient)
        p.setPen(QPen(styles.color(styles.WHITE, 0.08), 1))
        p.drawEllipse(center, cap_r, cap_r)

        # Indicateur blanc du centre vers le bord
        angle = math.radians(START_ANGLE - SPAN * pos)
        direction = QPointF(math.cos(angle), -math.sin(angle))
        width = max(2.0, d * 0.055)
        p.setPen(QPen(styles.color(styles.WHITE, 0.95 if self._active else 0.75), width,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(center + direction * (cap_r * 0.25), center + direction * (cap_r * 0.78))

    # ------------------------------------------------------------ interaction
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging, self._moved = True, False
        self._press_y = event.position().y()
        self._press_pos = _position(self._value)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._dragging:
            return
        dy = self._press_y - event.position().y()
        if not self._moved and abs(dy) < CLICK_SLOP:
            return
        self._moved = True
        factor = DRAG_FINE if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        self._user_set_position(self._press_pos + dy / DRAG_PX * factor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or not self._dragging:
            return
        self._dragging = False
        if not self._moved and self._checkable and self.rect().contains(event.position().toPoint()):
            self._active = not self._active
            self._update_tooltip()
            self.update()
            self.toggled.emit(self._active)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        # Sur un knob de routing, le double-clic serait aussi deux bascules : réservé au master
        if not self._checkable and event.button() == Qt.MouseButton.LeftButton:
            self._user_set(self._default)

    def wheelEvent(self, event) -> None:  # noqa: N802
        steps = event.angleDelta().y() / 120
        factor = DRAG_FINE if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        self._user_set_position(_position(min(self._value, FADER_MAX_DB)) + steps * WHEEL_STEP * factor)
        event.accept()

    def _user_set_position(self, pos: float) -> None:
        self._user_set(round(ratio_to_db(1.0 - max(0.0, min(1.0, pos))), 1))

    def _user_set(self, db: float) -> None:
        db = max(FADER_MIN_DB, min(FADER_MAX_DB, db))
        if abs(db - self._value) < 0.005:
            return
        self._value = db
        self._retarget()
        self.valueChanged.emit(db)

    # ----------------------------------------------------------- animation
    def _retarget(self) -> None:
        self._update_tooltip()
        if self._smooth:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._shown = _position(self._value)
            self.update()

    def _animate(self) -> None:
        target = _position(self._value)
        self._shown += (target - self._shown) * SMOOTH_FACTOR
        if abs(target - self._shown) < 0.001:
            self._shown = target
            self._timer.stop()
        self.update()

    def _update_tooltip(self) -> None:
        state = "" if not self._checkable else (" · activé" if self._active else " · coupé")
        self.setToolTip(f"{format_db(self._value)} dB{state}")
