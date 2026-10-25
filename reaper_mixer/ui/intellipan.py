"""
Intellipan, comme dans Voicemeeter : un pad 2D où l'on déplace un petit cube
rouge. En haut, le mode (VOIX / POSITION / EFFETS) : clic pour en changer.

- glisser dans le pad = déplacer le cube (envoyé à Voicemeeter en continu)
- double-clic = retour au neutre (0, 0)
- clic droit = choix du mode et remise au neutre

x va de -0,5 (gauche) à +0,5 (droite) ; y du bas (min) vers le haut (max),
avec la plage de l'API Remote du mode (voir core.voicemeeter_ctrl).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QMenu, QSizePolicy, QWidget

from core.voicemeeter_ctrl import PAD_MODES, PAD_X_RANGE, clamp_pad, pad_y_range
from ui import styles

PAD_HEIGHT = 66
HEADER_H = 15
CUBE = 11.0          # côté du petit cube (px)
GRID_COLUMNS, GRID_ROWS = 4, 3


class IntelliPad(QWidget):
    moved = Signal(str, float, float)     # (mode, x, y) choisis par l'utilisateur
    modeChanged = Signal(str)

    def __init__(self, modes: tuple[str, ...], physical: bool, mode: str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._modes = tuple(m for m in modes if m in PAD_MODES) or ("pan",)
        self._physical = physical
        self._mode = mode if mode in self._modes else self._modes[0]
        self._values: dict[str, tuple[float, float]] = {m: (0.0, 0.0) for m in self._modes}
        self._dragging = False
        self._hover_header = False
        self.color_scope: int | None = None     # canal (couleur personnalisée du cube)
        self.setFixedHeight(PAD_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._update_tooltip()

    # ------------------------------------------------------------------ API
    def mode(self) -> str:
        return self._mode

    def modes(self) -> tuple[str, ...]:
        return self._modes

    def value(self, mode: str | None = None) -> tuple[float, float]:
        return self._values.get(mode or self._mode, (0.0, 0.0))

    def set_mode(self, mode: str) -> None:
        if mode in self._modes and mode != self._mode:
            self._mode = mode
            self._update_tooltip()
            self.update()
            self.modeChanged.emit(mode)

    def set_values(self, pads: tuple[tuple[str, float, float], ...]) -> None:
        """Valeurs lues dans Voicemeeter ; celle du mode en cours est ignorée
        pendant un glisser (sinon le cube sauterait)."""
        changed = False
        for mode, x, y in pads:
            if mode not in self._values or (self._dragging and mode == self._mode):
                continue
            if self._values[mode] != (x, y):
                self._values[mode] = (x, y)
                changed = True
        if changed:
            self._update_tooltip()
            self.update()

    def isInteracting(self) -> bool:  # noqa: N802
        return self._dragging

    # ------------------------------------------------------------ géométrie
    def _area(self) -> QRectF:
        """Zone où se déplace le cube (sous l'en-tête du mode)."""
        return QRectF(1.5, HEADER_H + 1.5, self.width() - 3, self.height() - HEADER_H - 3)

    def _header(self) -> QRectF:
        return QRectF(0, 0, self.width(), HEADER_H)

    def _y_range(self) -> tuple[float, float]:
        return pad_y_range(self._mode, self._physical)

    def _to_point(self, x: float, y: float) -> QPointF:
        area = self._area().adjusted(CUBE / 2, CUBE / 2, -CUBE / 2, -CUBE / 2)
        low, high = self._y_range()
        fx = (x - PAD_X_RANGE[0]) / (PAD_X_RANGE[1] - PAD_X_RANGE[0])
        fy = (y - low) / (high - low)
        return QPointF(area.left() + fx * area.width(), area.bottom() - fy * area.height())

    def _from_point(self, point: QPointF) -> tuple[float, float]:
        area = self._area().adjusted(CUBE / 2, CUBE / 2, -CUBE / 2, -CUBE / 2)
        low, high = self._y_range()
        fx = (point.x() - area.left()) / max(1.0, area.width())
        fy = (area.bottom() - point.y()) / max(1.0, area.height())
        x = PAD_X_RANGE[0] + fx * (PAD_X_RANGE[1] - PAD_X_RANGE[0])
        return clamp_pad(self._mode, self._physical, x, low + fy * (high - low))

    # --------------------------------------------------------------- souris
    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        if event.button() == Qt.MouseButton.RightButton:
            self._show_menu(event.globalPosition().toPoint())   # interrompt un glisser
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._header().contains(pos):
            if len(self._modes) > 1:
                self._show_menu(self.mapToGlobal(self._header().bottomLeft().toPoint()))
            return
        self._dragging = True
        self._move_to(pos)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        if self._dragging:
            if event.buttons() & Qt.MouseButton.LeftButton:
                self._move_to(pos)
                return
            # Relâchement jamais reçu (menu ouvert pendant le glisser) : fin du glisser,
            # sinon le simple survol déplacerait le cube
            self._dragging = False
            self.update()
        over = self._header().contains(pos) and len(self._modes) > 1
        if over != self._hover_header:
            self._hover_header = over
            self.setCursor(Qt.CursorShape.PointingHandCursor if over
                           else Qt.CursorShape.CrossCursor)
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and not self._header().contains(event.position()):
            self.reset()

    def leaveEvent(self, _event) -> None:  # noqa: N802
        if self._hover_header:
            self._hover_header = False
            self.update()

    def reset(self) -> None:
        """Retour au neutre (0, 0) pour le mode en cours."""
        self._set_value(*clamp_pad(self._mode, self._physical, 0.0, 0.0))

    def _move_to(self, pos: QPointF) -> None:
        self._set_value(*self._from_point(pos))

    def _set_value(self, x: float, y: float) -> None:
        if self._values.get(self._mode) == (x, y):
            return
        self._values[self._mode] = (x, y)
        self._update_tooltip()
        self.update()
        self.moved.emit(self._mode, x, y)

    def _show_menu(self, where) -> None:
        # Le menu capte la souris : le relâchement du bouton gauche n'arrivera pas ici
        self._dragging = False
        menu = QMenu(self)
        for mode in self._modes:
            action = QAction(PAD_MODES[mode].title, menu)
            action.setCheckable(True)
            action.setChecked(mode == self._mode)
            action.triggered.connect(lambda _checked=False, m=mode: self.set_mode(m))
            menu.addAction(action)
        menu.addSeparator()
        reset = QAction("Remettre au neutre", menu)
        reset.triggered.connect(self.reset)
        menu.addAction(reset)
        menu.exec(where)

    def _update_tooltip(self) -> None:
        x, y = self.value()
        hint = "clic sur le titre ou clic droit = changer de mode, " if len(self._modes) > 1 \
            else "clic droit = menu, "
        self.setToolTip(f"Intellipan — {PAD_MODES[self._mode].title}\n"
                        f"x {x:+.2f} · y {y:+.2f}\n"
                        f"Glisser le cube pour régler, double-clic = neutre,\n{hint}"
                        "comme dans Voicemeeter.")

    # --------------------------------------------------------------- dessin
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        whole = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)

        # Fond : sombre, légèrement teinté par le thème
        background = QLinearGradient(0, 0, 0, self.height())
        background.setColorAt(0, QColor(styles.BG_DEEP))
        background.setColorAt(1, QColor(styles.BG_SECTION))
        p.setPen(QPen(QColor(styles.BORDER_COLUMN), 1))
        p.setBrush(background)
        p.drawRoundedRect(whole, 7, 7)

        # En-tête : mode en cours (cliquable s'il y a plusieurs modes)
        header = self._header()
        p.setFont(styles.font(8, QFont.Weight.DemiBold))
        p.setPen(QColor(styles.GREEN_NEON if self._hover_header else styles.TEXT_SECONDARY))
        label = PAD_MODES[self._mode].label + ("  ▾" if len(self._modes) > 1 else "")
        p.drawText(header.adjusted(7, 1, -6, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   label)
        p.setPen(QColor(styles.TEXT_MUTED))
        p.setFont(styles.font(8))
        x, y = self.value()
        p.drawText(header.adjusted(6, 1, -7, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"{x:+.2f} {y:+.2f}")

        # Quadrillage + axes centraux
        area = self._area().adjusted(2, 0, -2, -1)
        p.setPen(QPen(styles.color(styles.GREEN_DIM, 0.45), 1))
        for i in range(1, GRID_COLUMNS):
            gx = area.left() + area.width() * i / GRID_COLUMNS
            p.drawLine(QPointF(gx, area.top()), QPointF(gx, area.bottom()))
        for i in range(1, GRID_ROWS):
            gy = area.top() + area.height() * i / GRID_ROWS
            p.drawLine(QPointF(area.left(), gy), QPointF(area.right(), gy))
        p.setPen(QPen(styles.color(styles.GREEN_DIM, 0.9), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(area.center().x(), area.top()), QPointF(area.center().x(), area.bottom()))

        # Repères rouges vers les bords, puis le petit cube rouge (ou sa couleur choisie)
        cube_color = styles.accent("pad", self.color_scope)
        center = self._to_point(x, y)
        p.setPen(QPen(styles.color(cube_color, 0.28), 1, Qt.PenStyle.DotLine))
        p.drawLine(QPointF(area.left(), center.y()), QPointF(area.right(), center.y()))
        p.drawLine(QPointF(center.x(), area.top()), QPointF(center.x(), area.bottom()))

        glow = 7.0 if self._dragging else 4.5
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(styles.color(cube_color, 0.22))
        p.drawRoundedRect(QRectF(center.x() - CUBE / 2 - glow / 2, center.y() - CUBE / 2 - glow / 2,
                                 CUBE + glow, CUBE + glow), 4, 4)
        cube = QRectF(center.x() - CUBE / 2, center.y() - CUBE / 2, CUBE, CUBE)
        face = QLinearGradient(cube.topLeft(), cube.bottomRight())
        if styles.element_color("pad", self.color_scope) is None:    # rouge d'origine
            light, dark, edge = QColor("#ff7a7a"), QColor("#a8121b"), QColor("#3d0508")
        else:
            c = QColor(cube_color)
            light, dark, edge = c.lighter(145), c.darker(150), c.darker(420)
        face.setColorAt(0, light)
        face.setColorAt(0.45, QColor(cube_color))
        face.setColorAt(1, dark)
        p.setPen(QPen(edge, 1))
        p.setBrush(face)
        p.drawRoundedRect(cube, 2.2, 2.2)
        # Reflet : petit éclat en haut à gauche, effet « cube »
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(styles.color(styles.WHITE, 0.55))
        p.drawRoundedRect(QRectF(cube.left() + 2, cube.top() + 2, 3.2, 2.2), 1, 1)
