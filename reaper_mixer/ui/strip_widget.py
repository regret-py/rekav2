"""
Un strip complet, de haut en bas : effets (REAPER), 4 knobs de routing,
Intellipan (le carré au petit cube rouge de Voicemeeter), bouton Muet (et,
sur les entrées virtuelles, bouton « Apps » : volume par application),
fader + VU, nom éditable (double-clic).

Le strip ne parle à aucun moteur : il émet des signaux, la fenêtre principale
les relaie à Voicemeeter / REAPER, et lui renvoie l'état réel.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QPushButton, QScrollArea, QStackedLayout, QVBoxLayout,
                               QWidget)

from core.reaper_ctrl import TrackInfo
from core.voicemeeter_ctrl import StripParams
from ui import styles
from ui.fader import Fader, format_db
from ui.fx_pill import PILL_HEIGHT, FxPill
from ui.intellipan import PAD_HEIGHT, IntelliPad
from ui.knob import Knob
from ui.vu_meter import ClipLed, VuMeter

STRIP_WIDTH = 133
FX_SECTION_HEIGHT = 186
ROUTING_SECTION_HEIGHT = 102
PAD_SECTION_HEIGHT = PAD_HEIGHT + 8
VU_BAR_WIDTH, VU_GAP = 7, 3      # VU-mètres des strips (largeur d'une barre, écart)
MUTE_HEIGHT = 25
APPS_BUTTON_WIDTH = 50
NAME_HEIGHT = 24
SLOT_PITCH = PILL_HEIGHT + 2
MIN_SLOTS = 8
ROUTING_KNOB = 28


def make_section() -> QFrame:
    frame = QFrame()
    frame.setObjectName("section")
    return frame


# --------------------------------------------------------------------------- #
# Badge FX (bypass de toute la chaîne)
# --------------------------------------------------------------------------- #
class FxBadge(QAbstractButton):
    """Carré « FX ⏻ » : vert = chaîne active, sombre = chaîne en bypass."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(True)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(self._refresh_tooltip)
        self._refresh_tooltip(True)

    def _refresh_tooltip(self, checked: bool) -> None:
        self.setToolTip("Effets actifs — clic pour tout bypasser" if checked
                        else "Effets en bypass — clic pour les réactiver")

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(2, 2, self.width() - 4, self.height() - 4)
        on = self.isChecked() and self.isEnabled()   # sans piste REAPER : éteint
        if on:
            p.setPen(QPen(styles.color(styles.GREEN_NEON, 0.25), 3))
            p.setBrush(QColor(styles.GREEN_NEON))
            ink = QColor(styles.ON_ACCENT)
        else:
            p.setPen(QPen(QColor(styles.GREEN_DIM), 1.5))
            p.setBrush(QColor(styles.BADGE_OFF))
            ink = QColor(styles.TEXT_MUTED)
        if self.underMouse():
            p.setBrush(p.brush().color().lighter(112))
        p.drawRoundedRect(rect, 7, 7)

        p.setPen(ink)
        p.setFont(styles.font(7, QFont.Weight.DemiBold))
        p.drawText(QRectF(rect.x(), rect.y() + 2, rect.width(), 9), Qt.AlignmentFlag.AlignCenter, "FX")
        # Icône ⏻ : arc ouvert en haut + trait vertical
        c = QPointF(rect.center().x(), rect.y() + rect.height() * 0.66)
        r = rect.width() * 0.2
        p.setPen(QPen(ink, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), 125 * 16, 290 * 16)
        p.drawLine(QPointF(c.x(), c.y() - r - 1.5), QPointF(c.x(), c.y() - 0.5))

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)


# --------------------------------------------------------------------------- #
# Bouton « Apps » (volume par application)
# --------------------------------------------------------------------------- #
class AppsButton(QAbstractButton):
    """Icône de mélangeur + nombre d'applications qui jouent sur la strip."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._count = 0
        self.setFixedSize(APPS_BUTTON_WIDTH, MUTE_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Volume par application")

    def count(self) -> int:
        return self._count

    def set_count(self, count: int) -> None:
        if count != self._count:
            self._count = count
            self.setToolTip(f"Volume par application — {count} application(s)" if count
                            else "Volume par application — aucune application ne joue ici")
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        hover = self.underMouse() or self.isDown()
        p.setPen(QPen(QColor(styles.GREEN_DIM if hover else styles.BORDER_COLUMN), 1))
        p.setBrush(QColor(styles.BUTTON_HOVER if hover else styles.BUTTON_BG))
        p.drawRoundedRect(rect, 7, 7)
        # Icône : trois glissières horizontales
        ink = QColor(styles.TEXT_PRIMARY)
        p.setPen(QPen(ink, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        left, top = 9.0, self.height() / 2 - 5
        for n, knob in enumerate((0.7, 0.3, 0.55)):
            y = top + n * 5
            p.drawLine(QPointF(left, y), QPointF(left + 12, y))
            p.setBrush(ink)
            p.drawEllipse(QPointF(left + 12 * knob, y), 1.8, 1.8)
        p.setFont(styles.font(10, QFont.Weight.DemiBold))
        p.setPen(QColor(styles.GREEN_NEON if self._count else styles.TEXT_MUTED))
        p.drawText(QRectF(left + 15, 0, self.width() - left - 19, self.height()),
                   Qt.AlignmentFlag.AlignCenter, str(self._count))

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)


# --------------------------------------------------------------------------- #
# Liste des effets
# --------------------------------------------------------------------------- #
class FxList(QWidget):
    """Pills des plugins puis emplacements vides (« + ») : un clic sur un
    emplacement vide ouvre le dialogue d'ajout."""

    addRequested = Signal()
    openRequested = Signal(int)
    bypassRequested = Signal(int, bool)
    removeRequested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pills: list[FxPill] = []
        self._hover_slot = -1
        self._enabled = True
        self.setMouseTracking(True)
        self._resize_for(0)

    def set_effects(self, effects: list[tuple[str, bool, bool]]) -> None:
        """effects = [(nom affiché, actif, interface ouverte), …]"""
        while len(self._pills) > len(effects):
            self._pills.pop().deleteLater()
        for index, (name, enabled, is_open) in enumerate(effects):
            if index < len(self._pills):
                self._pills[index].set_info(name, enabled, is_open)
                continue
            pill = FxPill(name, enabled, is_open, self)
            pill.clicked.connect(lambda i=index: self.openRequested.emit(i))
            pill.bypassRequested.connect(lambda on, i=index: self.bypassRequested.emit(i, on))
            pill.removeRequested.connect(lambda i=index: self.removeRequested.emit(i))
            pill.show()
            self._pills.append(pill)
        self._resize_for(len(effects))
        self._layout_pills()
        self.update()

    def set_available(self, available: bool) -> None:
        """REAPER déconnecté : on ne propose plus d'ajouter d'effet."""
        self._enabled = available
        self.update()

    def _resize_for(self, count: int) -> None:
        # Les pills + un emplacement libre ; les emplacements vides suivants
        # sont seulement dessinés (la zone ne défile que s'il y a beaucoup d'effets)
        self.setMinimumHeight((count + 1) * SLOT_PITCH)

    def _layout_pills(self) -> None:
        for index, pill in enumerate(self._pills):
            pill.setGeometry(0, index * SLOT_PITCH, self.width(), PILL_HEIGHT)

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._layout_pills()
        super().resizeEvent(event)

    def _slot_at(self, y: float) -> int:
        return int(y // SLOT_PITCH)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        slot = self._slot_at(event.position().y())
        slot = slot if slot >= len(self._pills) else -1
        if slot != self._hover_slot:
            self._hover_slot = slot
            self.setCursor(Qt.CursorShape.PointingHandCursor if slot >= 0
                           else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, _event) -> None:  # noqa: N802
        self._hover_slot = -1
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if (event.button() == Qt.MouseButton.LeftButton and self._enabled
                and self._slot_at(event.position().y()) >= len(self._pills)):
            self.addRequested.emit()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        slots = max(MIN_SLOTS, (self.height() + 2) // SLOT_PITCH)
        dash = QPen(styles.color(styles.TEXT_MUTED, 0.18), 1, Qt.PenStyle.DotLine)
        for slot in range(len(self._pills), slots):
            y = slot * SLOT_PITCH
            rect = QRectF(0, y, self.width(), PILL_HEIGHT)
            if slot > len(self._pills) or self._pills:
                p.setPen(dash)
                p.drawLine(QPointF(4, y - 1), QPointF(self.width() - 4, y - 1))
            if slot == self._hover_slot and self._enabled:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(styles.color(styles.GREEN_DIM, 0.35))
                p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
                p.setPen(QColor(styles.GREEN_NEON))
                p.setFont(styles.font(10, QFont.Weight.Medium))
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "+ Ajouter un effet")
            else:
                p.setPen(QPen(styles.color(styles.TEXT_MUTED, 0.55), 1.2))
                cx, cy = rect.center().x(), rect.center().y()
                p.drawLine(QPointF(cx - 2, cy), QPointF(cx + 2, cy))
                p.drawLine(QPointF(cx, cy - 2), QPointF(cx, cy + 2))


# --------------------------------------------------------------------------- #
# Nom éditable
# --------------------------------------------------------------------------- #
class EditableLabel(QWidget):
    """Label centré ; double-clic pour le renommer (Entrée valide, Échap annule)."""

    edited = Signal(str)

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = QLabel(text)
        self._label.setObjectName("stripName")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setToolTip("Double-clic ou clic droit pour renommer")
        self._edit = QLineEdit(text)
        self._edit.setObjectName("stripNameEdit")
        self._edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._edit.setMaxLength(30)
        self._edit.editingFinished.connect(self._commit)
        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self._label)
        self._stack.addWidget(self._edit)

    def text(self) -> str:
        return self._label.text()

    def setText(self, text: str) -> None:  # noqa: N802
        self._label.setText(text)

    def mouseDoubleClickEvent(self, _event) -> None:  # noqa: N802
        self.start_editing()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        menu.addAction("Renommer le canal…", self.start_editing)
        menu.exec(event.globalPos())

    def start_editing(self) -> None:
        self._edit.setText(self._label.text())
        self._stack.setCurrentWidget(self._edit)
        self._edit.setFocus()
        self._edit.selectAll()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # Échap remonte du QLineEdit jusqu'ici : on annule sans valider
        if event.key() == Qt.Key.Key_Escape:
            self._stack.setCurrentWidget(self._label)
        super().keyPressEvent(event)

    def _commit(self) -> None:
        if self._stack.currentWidget() is not self._edit:
            return
        self._stack.setCurrentWidget(self._label)
        text = self._edit.text().strip()
        if text and text != self._label.text():
            self._label.setText(text)
            self.edited.emit(text)


# --------------------------------------------------------------------------- #
# Strip
# --------------------------------------------------------------------------- #
class StripWidget(QFrame):
    gainChanged = Signal(int, float)
    muteToggled = Signal(int, bool)
    routeToggled = Signal(int, int, bool)
    sendChanged = Signal(int, int, float)
    renamed = Signal(int, str)
    chainToggled = Signal(int, bool)
    fxAddRequested = Signal(int)
    fxOpenRequested = Signal(int, int)
    fxBypassRequested = Signal(int, int, bool)
    fxRemoveRequested = Signal(int, int)
    padMoved = Signal(int, str, float, float)    # (strip, mode, x, y)
    padModeChanged = Signal(int, str)
    appsRequested = Signal(int)

    def __init__(self, index: int, name: str, route_labels: list[str],
                 sends_adjustable: bool = True, pad_modes: tuple[str, ...] = ("color", "pan", "fx"),
                 physical: bool = True, pad_mode: str | None = None, apps_button: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.index = index
        self._sends_adjustable = sends_adjustable   # False : Banana, knobs = interrupteurs
        self.setObjectName("column")
        self.setFixedWidth(STRIP_WIDTH)
        self._track: TrackInfo | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 4)
        layout.setSpacing(8)
        layout.addWidget(self._build_fx_section())
        layout.addWidget(self._build_routing_section(route_labels))
        layout.addWidget(self._build_pad_section(pad_modes, physical, pad_mode))

        self.mute = QPushButton("Muet")
        self.mute.setObjectName("muteButton")
        self.mute.setCheckable(True)
        self.mute.setFixedHeight(MUTE_HEIGHT)
        self.mute.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mute.toggled.connect(self._on_mute)
        self.apps: AppsButton | None = None
        if apps_button:
            row = QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(self.mute, 1)
            self.apps = AppsButton()
            self.apps.clicked.connect(lambda: self.appsRequested.emit(self.index))
            row.addWidget(self.apps)
            layout.addLayout(row)
        else:
            layout.addWidget(self.mute)

        layout.addWidget(self._build_fader_section(), 1)

        self.name = EditableLabel(name)
        self.name.setFixedHeight(NAME_HEIGHT)
        self.name.edited.connect(lambda text: self.renamed.emit(self.index, text))
        layout.addWidget(self.name)

    # ------------------------------------------------------------ sections
    def _build_fx_section(self) -> QFrame:
        section = make_section()
        section.setFixedHeight(FX_SECTION_HEIGHT)
        box = QVBoxLayout(section)
        box.setContentsMargins(5, 5, 5, 5)
        box.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(2, 0, 0, 0)
        title = QLabel("Effets")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.fx_badge = FxBadge()
        self.fx_badge.clicked.connect(lambda: self.chainToggled.emit(self.index, self.fx_badge.isChecked()))
        header.addWidget(self.fx_badge)
        box.addLayout(header)

        self.fx_list = FxList()
        self.fx_list.addRequested.connect(lambda: self.fxAddRequested.emit(self.index))
        self.fx_list.openRequested.connect(lambda i: self.fxOpenRequested.emit(self.index, i))
        self.fx_list.bypassRequested.connect(lambda i, on: self.fxBypassRequested.emit(self.index, i, on))
        self.fx_list.removeRequested.connect(lambda i: self.fxRemoveRequested.emit(self.index, i))
        scroll = QScrollArea()
        scroll.setObjectName("fxScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.fx_list)
        scroll.viewport().setAutoFillBackground(False)
        self.fx_list.setAutoFillBackground(False)
        box.addWidget(scroll, 1)
        return section

    def _build_routing_section(self, labels: list[str]) -> QFrame:
        section = make_section()
        section.setFixedHeight(ROUTING_SECTION_HEIGHT)
        grid = QGridLayout(section)
        grid.setContentsMargins(4, 6, 4, 4)
        grid.setHorizontalSpacing(2)
        grid.setVerticalSpacing(0)
        self.knobs: list[Knob] = []
        for route, label_text in enumerate(labels):
            cell = QVBoxLayout()
            cell.setSpacing(0)
            knob = Knob(diameter=ROUTING_KNOB, checkable=True, adjustable=self._sends_adjustable)
            knob.toggled.connect(lambda on, r=route: self.routeToggled.emit(self.index, r, on))
            knob.valueChanged.connect(lambda db, r=route: self.sendChanged.emit(self.index, r, db))
            label = QLabel(label_text)
            label.setObjectName("knobLabel")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.addWidget(knob, 0, Qt.AlignmentFlag.AlignHCenter)
            cell.addWidget(label)
            grid.addLayout(cell, route // 2, route % 2)
            self.knobs.append(knob)
        return section

    def _build_pad_section(self, modes: tuple[str, ...], physical: bool,
                           mode: str | None) -> QFrame:
        section = make_section()
        section.setFixedHeight(PAD_SECTION_HEIGHT)
        box = QVBoxLayout(section)
        box.setContentsMargins(4, 4, 4, 4)
        self.pad = IntelliPad(modes, physical, mode)
        self.pad.moved.connect(lambda m, x, y: self.padMoved.emit(self.index, m, x, y))
        self.pad.modeChanged.connect(lambda m: self.padModeChanged.emit(self.index, m))
        box.addWidget(self.pad)
        return section

    def _build_fader_section(self) -> QFrame:
        section = make_section()
        box = QVBoxLayout(section)
        box.setContentsMargins(8, 6, 8, 8)
        box.setSpacing(4)

        header = QHBoxLayout()
        self.clip = ClipLed()
        header.addWidget(self.clip, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)
        self.value_label = QLabel()
        self.value_label.setObjectName("faderValue")
        self.value_label.setTextFormat(Qt.TextFormat.RichText)
        header.addWidget(self.value_label)
        box.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(6)
        body.addStretch(1)
        self.fader = Fader()
        self.fader.valueChanged.connect(self._on_fader)
        body.addWidget(self.fader)
        self.vu = VuMeter(channels=2, bar_width=VU_BAR_WIDTH, gap=VU_GAP)
        body.addWidget(self.vu)
        body.addStretch(1)
        box.addLayout(body, 1)
        self._show_value(0.0)
        return section

    # ---------------------------------------------------- état venant des moteurs
    def set_params(self, params: StripParams) -> None:
        self.fader.setValue(params.gain)
        if not self.fader.isInteracting():
            self._show_value(params.gain)
        if self.mute.isChecked() != params.mute:
            self.mute.blockSignals(True)
            self.mute.setChecked(params.mute)
            self.mute.blockSignals(False)
            self._apply_mute_look(params.mute)
        for knob, enabled, send in zip(self.knobs, params.routes, params.sends):
            if not knob.isInteracting():
                knob.setActive(enabled)
                knob.setValue(send)
        self.pad.set_values(params.pads)

    def set_levels(self, levels: tuple[float, float]) -> None:
        self.vu.set_levels(levels)
        self.clip.set_lit(self.vu.is_clipping())

    def set_track(self, track: TrackInfo | None) -> None:
        """Piste REAPER de la strip (None : aucune piste du projet n'écoute ses canaux)."""
        self._track = track
        if track is None:
            self.fx_list.set_effects([])
            self.fx_badge.setEnabled(False)
            self.fx_badge.setToolTip("Aucune piste REAPER pour cette strip : "
                                     "ajoute un effet pour en créer une.")
            return
        self.fx_list.set_effects([(fx.display_name, fx.enabled, fx.open) for fx in track.fx])
        if self.fx_badge.isChecked() != track.fx_enabled:
            self.fx_badge.blockSignals(True)
            self.fx_badge.setChecked(track.fx_enabled)
            self.fx_badge.blockSignals(False)
            self.fx_badge.update()
        if track.processes_live:
            self.fx_badge._refresh_tooltip(self.fx_badge.isChecked())
        else:
            self.fx_badge.setToolTip(f"La piste REAPER « {track.name} » n'est pas armée en "
                                     "écoute : ses effets ne s'appliquent pas au son.")

    def set_reaper_available(self, available: bool, can_add: bool = True) -> None:
        """REAPER joignable ? `can_add` : un effet peut être ajouté (la strip a
        une piste, ou l'app a le droit d'en créer une)."""
        self.fx_list.set_available(available and can_add)
        self.fx_badge.setEnabled(available and self._track is not None)

    def fx_name(self, fx_index: int) -> str:
        if self._track is not None and 0 <= fx_index < len(self._track.fx):
            return self._track.fx[fx_index].display_name
        return ""

    def strip_name(self) -> str:
        return self.name.text()

    # ------------------------------------------------------------ interne
    def _on_fader(self, db: float) -> None:
        self._show_value(db)
        self.gainChanged.emit(self.index, db)

    def _on_mute(self, muted: bool) -> None:
        self._apply_mute_look(muted)
        self.muteToggled.emit(self.index, muted)

    def _apply_mute_look(self, muted: bool) -> None:
        self.fader.setMuted(muted)
        self.value_label.setProperty("muted", muted)
        self.value_label.style().unpolish(self.value_label)
        self.value_label.style().polish(self.value_label)

    def _show_value(self, db: float) -> None:
        self.value_label.setText(
            f"{format_db(db)}<span style='color:{styles.TEXT_MUTED}; font-size:9px;'> dB</span>")
