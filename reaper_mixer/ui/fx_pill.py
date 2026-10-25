"""
Pill d'un plugin VST dans la section Effets d'un strip.

Clic = ouvrir / fermer l'interface du plugin. Clic droit = menu (ouvrir,
bypass, supprimer). Un plugin en bypass est grisé et barré ; un plugin dont
l'interface est ouverte a une bordure verte.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QMenu, QSizePolicy, QWidget

from ui import styles

PILL_HEIGHT = 16
PADDING_X = 7
SLOW_PLUGIN_MS = 3.0     # au-delà, point ambre : plugin qui retarde nettement le son


class FxPill(QWidget):
    clicked = Signal()                 # ouvrir / fermer l'interface
    bypassRequested = Signal(bool)     # True = réactiver, False = bypass
    removeRequested = Signal()

    def __init__(self, name: str, enabled: bool = True, is_open: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = name
        self._enabled = enabled
        self._open = is_open
        self._hover = False
        self._pressed = False
        self._latency_ms: float | None = None
        self.color_scope: int | None = None     # canal (couleur personnalisée)
        self.setFixedHeight(PILL_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_tooltip()

    def set_info(self, name: str, enabled: bool, is_open: bool) -> None:
        if (name, enabled, is_open) != (self._name, self._enabled, self._open):
            self._name, self._enabled, self._open = name, enabled, is_open
            self._refresh_tooltip()
            self.update()

    def set_latency(self, ms: float | None) -> None:
        """Latence propre du plugin (compensation), None si inconnue."""
        if ms != self._latency_ms:
            self._latency_ms = ms
            self._refresh_tooltip()
            self.update()

    @property
    def name(self) -> str:
        return self._name

    @property
    def slow(self) -> bool:
        return self._latency_ms is not None and self._latency_ms >= SLOW_PLUGIN_MS

    # -------------------------------------------------------------- dessin
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)

        custom = styles.element_color("fx_pill", self.color_scope)
        if self._open:
            border = styles.color(custom or styles.GREEN_NEON, 0.9)
        elif self._hover:
            border = styles.color(styles.accent_bright("fx_pill", self.color_scope), 0.55)
        else:
            border = QColor(styles.mix(styles.PILL_BORDER, custom, 0.55) if custom else styles.PILL_BORDER)
        base = styles.mix(styles.PILL_BG, custom, 0.3) if custom else styles.PILL_BG
        fill = QColor(base).lighter(125 if self._hover else 100)
        p.setPen(QPen(border, 1))
        p.setBrush(fill)
        p.drawRoundedRect(rect, 5, 5)

        f = styles.font(10, QFont.Weight.Medium)
        f.setStrikeOut(not self._enabled)
        p.setFont(f)
        p.setPen(QColor(styles.TEXT_PRIMARY if self._enabled else styles.TEXT_MUTED))
        text_rect = rect.adjusted(PADDING_X, 0, -PADDING_X - (8 if self.slow else 0), 0)
        text = p.fontMetrics().elidedText(self._name, Qt.TextElideMode.ElideRight,
                                          int(text_rect.width()))
        p.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        if self.slow:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(styles.SOLO_ON if self._enabled else styles.TEXT_MUTED))
            p.drawEllipse(rect.right() - 8.5, rect.center().y() - 2.5, 5, 5)

    # ------------------------------------------------------------ interaction
    def enterEvent(self, _event) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, _event) -> None:  # noqa: N802
        self._hover = False
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._pressed = event.button() == Qt.MouseButton.LeftButton

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if (self._pressed and event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.clicked.emit()
        self._pressed = False

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        open_action = menu.addAction("Fermer l'interface" if self._open else "Ouvrir l'interface")
        bypass_action = menu.addAction("Réactiver" if not self._enabled else "Désactiver (bypass)")
        menu.addSeparator()
        remove_action = menu.addAction("Supprimer")
        chosen = menu.exec(event.globalPos())
        if chosen is open_action:
            self.clicked.emit()
        elif chosen is bypass_action:
            self.bypassRequested.emit(not self._enabled)
        elif chosen is remove_action:
            self.removeRequested.emit()

    def _refresh_tooltip(self) -> None:
        state = "actif" if self._enabled else "désactivé (bypass)"
        latency = ""
        if self._latency_ms is not None:
            latency = f"\nLatence : {self._latency_ms:.1f} ms"
            if self.slow:
                latency += " (retarde nettement la voix : préfère un mode « zéro latence »)"
        self.setToolTip(f"{self._name} — {state}{latency}\nClic : interface · Clic droit : options")
