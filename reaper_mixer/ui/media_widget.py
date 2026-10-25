"""
Lecteur du Master : pochette de la musique en cours, titre, artiste, et
boutons précédent / lecture-pause / suivant (l'application qui joue :
Spotify, Deezer, navigateur… via les contrôles média de Windows).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QAbstractButton, QFrame, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from core.media import MediaInfo
from ui import styles

COVER_SIZE = 96
PLAY_SIZE = 30
SKIP_SIZE = 26


def _accent() -> str:
    return styles.accent("media", styles.MASTER_SCOPE)


class CoverView(QWidget):
    """Pochette aux coins arrondis ; une note de musique quand il n'y en a pas."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._scaled: QPixmap | None = None
        self.setFixedSize(COVER_SIZE, COVER_SIZE)

    def has_cover(self) -> bool:
        return self._pixmap is not None

    def set_cover(self, data: bytes) -> None:
        pixmap = QPixmap()
        self._pixmap = pixmap if data and pixmap.loadFromData(data) else None
        self._scaled = None
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(0, 0, self.width(), self.height())
        clip = QPainterPath()
        clip.addRoundedRect(rect, 8, 8)
        if self._pixmap is not None:
            if self._scaled is None:
                self._scaled = self._pixmap.scaled(
                    self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
            p.setClipPath(clip)
            p.drawPixmap(int((self.width() - self._scaled.width()) / 2),
                         int((self.height() - self._scaled.height()) / 2), self._scaled)
            p.setClipping(False)
            p.setPen(QPen(styles.color(styles.WHITE, 0.08), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
            return
        p.setPen(QPen(QColor(styles.BORDER_COLUMN), 1))
        p.setBrush(QColor(styles.BG_DEEP))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        # Note de musique (deux croches)
        ink = styles.color(styles.TEXT_MUTED, 0.9)
        c = rect.center()
        p.setPen(QPen(ink, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        left, right = QPointF(c.x() - 9, c.y() + 10), QPointF(c.x() + 11, c.y() + 6)
        p.drawLine(left + QPointF(5, 0), QPointF(left.x() + 5, c.y() - 12))
        p.drawLine(right + QPointF(5, 0), QPointF(right.x() + 5, c.y() - 16))
        p.drawLine(QPointF(left.x() + 5, c.y() - 12), QPointF(right.x() + 5, c.y() - 16))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink)
        p.drawEllipse(left, 6, 4.5)
        p.drawEllipse(right, 6, 4.5)


class ElidedLabel(QWidget):
    """Une ligne de texte, coupée par « … » si elle dépasse."""

    def __init__(self, size: int, weight: QFont.Weight, secondary: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = ""
        self._size, self._weight, self._secondary = size, weight, secondary
        self.setFixedHeight(size + 5)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:  # noqa: N802
        if text != self._text:
            self._text = text
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setFont(styles.font(self._size, self._weight))
        p.setPen(QColor(styles.TEXT_SECONDARY if self._secondary else styles.TEXT_PRIMARY))
        text = p.fontMetrics().elidedText(self._text, Qt.TextElideMode.ElideRight, self.width())
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)


class TransportButton(QAbstractButton):
    """Bouton rond : « previous », « play » (lecture / pause) ou « next »."""

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self._playing = False
        size = PLAY_SIZE if kind == "play" else SKIP_SIZE
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip({"previous": "Morceau précédent", "next": "Morceau suivant"}.get(kind, "Lecture"))

    def is_playing(self) -> bool:
        return self._playing

    def set_playing(self, playing: bool) -> None:
        if playing != self._playing:
            self._playing = playing
            self.setToolTip("Pause" if playing else "Lecture")
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        hover = self.underMouse() and self.isEnabled()
        accent = _accent()
        if self.kind == "play":
            fill = styles.accent_bright("media", styles.MASTER_SCOPE) if hover else accent
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(fill) if self.isEnabled() else QColor(styles.BUTTON_BG))
            ink = QColor(styles.ink_on(fill) if self.isEnabled() else styles.TEXT_MUTED)
        else:
            p.setPen(QPen(QColor(styles.accent_dim("media", styles.MASTER_SCOPE) if hover
                                 else styles.BORDER_COLUMN), 1))
            p.setBrush(QColor(styles.BUTTON_HOVER if hover else styles.BUTTON_BG))
            ink = QColor(styles.TEXT_PRIMARY if self.isEnabled() else styles.TEXT_MUTED)
        p.drawEllipse(rect)

        c = rect.center()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink)
        if self.kind == "play" and self._playing:            # pause : deux barres
            p.drawRoundedRect(QRectF(c.x() - 5, c.y() - 6, 3.6, 12), 1, 1)
            p.drawRoundedRect(QRectF(c.x() + 1.4, c.y() - 6, 3.6, 12), 1, 1)
        elif self.kind == "play":                             # lecture : triangle
            p.drawPolygon([QPointF(c.x() - 3.5, c.y() - 6.5), QPointF(c.x() + 6.5, c.y()),
                           QPointF(c.x() - 3.5, c.y() + 6.5)])
        else:                                                 # précédent / suivant : barre + triangle
            sign = 1 if self.kind == "next" else -1
            tip = c.x() + 3 * sign
            p.drawPolygon([QPointF(tip - 7 * sign, c.y() - 5), QPointF(tip, c.y()),
                           QPointF(tip - 7 * sign, c.y() + 5)])
            bar_x = tip if sign > 0 else tip - 2
            p.drawRoundedRect(QRectF(bar_x, c.y() - 5, 2, 10), 0.8, 0.8)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)


class MediaWidget(QFrame):
    """Bloc « musique en cours » du Master."""

    previousRequested = Signal()
    playPauseRequested = Signal()
    nextRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("section")
        self._info: MediaInfo | None = None
        box = QVBoxLayout(self)
        box.setContentsMargins(6, 8, 6, 8)
        box.setSpacing(3)
        self.cover = CoverView()
        box.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignHCenter)
        box.addSpacing(3)
        self.title = ElidedLabel(11, QFont.Weight.DemiBold)
        box.addWidget(self.title)
        self.artist = ElidedLabel(10, QFont.Weight.Medium, secondary=True)
        box.addWidget(self.artist)
        box.addSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addStretch(1)
        self.previous = TransportButton("previous")
        self.previous.clicked.connect(self.previousRequested)
        self.play = TransportButton("play")
        self.play.clicked.connect(self._on_play)
        self.next = TransportButton("next")
        self.next.clicked.connect(self.nextRequested)
        for button in (self.previous, self.play, self.next):
            row.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        box.addLayout(row)
        self.set_info(None)

    def info(self) -> MediaInfo | None:
        return self._info

    def set_info(self, info: MediaInfo | None) -> None:
        previous = self._info
        self._info = info
        if info is None:
            self.title.setText("Rien en lecture")
            self.artist.setText("")
            self.cover.set_cover(b"")
            self.setToolTip("Lance de la musique (Spotify, Deezer, navigateur…) : "
                            "elle s'affiche ici.")
            for button in (self.previous, self.play, self.next):
                button.setEnabled(False)
            self.play.set_playing(False)
            return
        self.title.setText(info.title)
        self.artist.setText(info.artist or info.app)
        if previous is None or previous.cover != info.cover:
            self.cover.set_cover(info.cover)
        lines = [f"{info.title} — {info.artist}" if info.artist else info.title]
        if info.album:
            lines.append(info.album)
        if info.app:
            lines.append(f"Dans : {info.app}")
        self.setToolTip("\n".join(lines))
        self.previous.setEnabled(info.can_previous)
        self.next.setEnabled(info.can_next)
        self.play.setEnabled(info.can_play_pause)
        self.play.set_playing(info.playing)

    def _on_play(self) -> None:
        # Retour immédiat : l'application confirme (ou non) à la relecture suivante
        self.play.set_playing(not self.play.is_playing())
        self.playPauseRequested.emit()
