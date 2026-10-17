"""
Palette, thèmes de couleur, polices (Inter embarquée) et feuille de style QSS.

Les widgets dessinés à la main (knob, fader, VU, pills) lisent directement
les couleurs ci-dessous (styles.GREEN_NEON…) à chaque dessin ; le reste passe
par la QSS appliquée à l'application.

Thèmes : la palette de référence est verte. Un thème donne une couleur
d'accent ; toutes les teintes vertes (accent, fonds teintés, bordures, textes
gris-vert) tournent vers sa teinte, en gardant leur luminosité. Le rouge du
mute, le jaune des VU et le blanc ne changent pas.
"""

from __future__ import annotations

import logging

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

from core import config

log = logging.getLogger(__name__)

# --- Palette de référence --------------------------------------------------
BG_BLACK = "#000000"
BG_PANEL = "#0a1510"      # Fond strip subtil
BG_PILL = "#0a1510"       # Fond des pills FX
GREEN_NEON = "#00ff88"    # Accent principal
GREEN_BRIGHT = "#00d4aa"  # Hover
GREEN_DIM = "#1a4030"     # Éléments inactifs
GREEN_TEXT = "#00ff88"
GREEN_LABEL = "#4d8870"   # Labels discrets
GREEN_SCALE = "#004d2a"   # Graduation dB
WHITE = "#ffffff"
RED_MUTE = "#ff2d2d"
YELLOW_VU = "#ffcc00"

# --- Compléments relevés sur la maquette ------------------------------------
BG_WINDOW = "#040b07"         # fond général (sous le dégradé)
BG_GLOW = "#001a0f"           # halo radial, centre-droit
BG_COLUMN = "#0e1612"         # colonnes (strips, master)
BG_SECTION = "#070e0a"        # blocs internes (effets, knobs, fader)
BORDER_COLUMN = "#16201b"
BORDER_SECTION = "#111a15"
TEXT_PRIMARY = "#e2e8e5"      # noms, valeurs
TEXT_SECONDARY = "#a3ada8"    # labels
TEXT_MUTED = "#5f6b65"
BUTTON_BG = "#1b2a24"
BUTTON_HOVER = "#223630"
PILL_BG = "#15201b"
PILL_BORDER = "#24332c"
KNOB_CAP_TOP = "#3a4642"      # dégradé du bouton des knobs
KNOB_CAP_BOTTOM = "#1c2421"
RED_DIM = "#3a1416"           # LED de saturation éteinte
TEXT_ON_RED = "#2b0707"
BG_DEEP = "#030b06"           # fonds très sombres (badge, bouton projet, Intellipan)
ON_ACCENT = "#00341c"         # encre sur l'accent (badge FX allumé)
ON_ACCENT_DARK = "#00140a"    # texte des boutons principaux
BADGE_OFF = "#0d1a14"         # badge FX éteint

# --- Thèmes ----------------------------------------------------------------
DEFAULT_THEME = "neon"
THEMES = {                    # clé (config ui.theme) → (nom affiché, accent)
    "neon": ("Vert néon", "#00ff88"),
    "cyan": ("Cyan", "#00e5ff"),
    "bleu": ("Bleu", "#3d8bff"),
    "violet": ("Violet", "#a66bff"),
    "rose": ("Rose", "#ff4fb8"),
    "rouge": ("Rouge", "#ff3b4f"),
    "orange": ("Orange", "#ff9a1f"),
    "or": ("Or", "#ffd23f"),
    "blanc": ("Blanc", "#e9eef0"),
}
# Couleurs qui suivent le thème (les autres sont fixes)
_THEMED = ("BG_PANEL", "BG_PILL", "GREEN_NEON", "GREEN_BRIGHT", "GREEN_DIM", "GREEN_TEXT",
           "GREEN_LABEL", "GREEN_SCALE", "BG_WINDOW", "BG_GLOW", "BG_COLUMN", "BG_SECTION",
           "BORDER_COLUMN", "BORDER_SECTION", "TEXT_PRIMARY", "TEXT_SECONDARY", "TEXT_MUTED",
           "BUTTON_BG", "BUTTON_HOVER", "PILL_BG", "PILL_BORDER", "KNOB_CAP_TOP",
           "KNOB_CAP_BOTTOM", "BG_DEEP", "ON_ACCENT", "ON_ACCENT_DARK", "BADGE_OFF")
_BASE = {name: globals()[name] for name in _THEMED}
MIN_ACCENT_CONTRAST = 4.5     # contraste minimal accent / fond (lisibilité, WCAG AA)
HOVER_TOWARD_WHITE = 0.22     # survol des boutons principaux : accent éclairci
_current_theme = DEFAULT_THEME

FONT_FAMILY = "Inter"
FONT_FALLBACK = "Segoe UI"
FONT_FILES = ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf")


def font(pixel_size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Police de l'interface (Inter, repli Segoe UI) en taille pixel."""
    f = QFont()
    f.setFamilies([FONT_FAMILY, FONT_FALLBACK])
    f.setPixelSize(pixel_size)
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def color(value: str, alpha: float = 1.0) -> QColor:
    c = QColor(value)
    c.setAlphaF(alpha)
    return c


def load_fonts() -> bool:
    """Charge Inter depuis assets/ ; renvoie False si elle est indisponible."""
    loaded = False
    for name in FONT_FILES:
        path = config.resource_path(f"assets/{name}")
        if path.is_file() and QFontDatabase.addApplicationFont(str(path)) >= 0:
            loaded = True
        else:
            log.warning("Police introuvable ou illisible : %s", path)
    return loaded


def theme_accent(theme: str | None) -> QColor | None:
    """Couleur d'accent d'un thème (clé de THEMES ou « #rrggbb »), None si invalide."""
    if theme in THEMES:
        return QColor(THEMES[theme][1])
    accent = QColor(str(theme or ""))
    return accent if str(theme or "").startswith("#") and accent.isValid() else None


def luminance(color: QColor) -> float:
    """Luminance relative (WCAG) d'une couleur."""
    def channel(value: int) -> float:
        v = value / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(color.red()) + 0.7152 * channel(color.green()) + 0.0722 * channel(color.blue())


def contrast(a: QColor, b: QColor) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _mix(a: QColor, b: QColor, amount: float) -> QColor:
    return QColor(round(a.red() + (b.red() - a.red()) * amount),
                  round(a.green() + (b.green() - a.green()) * amount),
                  round(a.blue() + (b.blue() - a.blue()) * amount))


def _rotate(value: str, hue_shift: float, saturation: float) -> str:
    h, s, l, _a = QColor(value).getHslF()
    if h < 0:                        # gris pur : pas de teinte à tourner
        return value
    return QColor.fromHslF((h + hue_shift) % 1.0, max(0.0, min(1.0, s * saturation)), l).name()


def set_theme(theme: str | None) -> str:
    """Recolore la palette d'après le thème ; renvoie le thème appliqué (le
    thème par défaut si `theme` est inconnu). Appeler ensuite refresh()."""
    global _current_theme
    accent = theme_accent(theme)
    if accent is None:
        theme, accent = DEFAULT_THEME, QColor(THEMES[DEFAULT_THEME][1])
    values = dict(_BASE)
    if theme != DEFAULT_THEME:
        ah, as_, al, _ = accent.getHslF()
        bh, bs, _bl, _ = QColor(_BASE["GREEN_NEON"]).getHslF()
        shift = ah - bh if ah >= 0 else 0.0
        saturation = as_ / bs if ah >= 0 and bs > 0 else 0.0
        values = {name: _rotate(value, shift, saturation) for name, value in _BASE.items()}
        # Accent assez clair pour se lire sur les fonds (un bleu pur #0000ff serait
        # presque invisible) : on l'éclaircit jusqu'au contraste voulu
        background = QColor(values["BG_COLUMN"])
        while contrast(accent, background) < MIN_ACCENT_CONTRAST and al < 0.95:
            al = min(0.95, al + 0.02)
            accent = QColor.fromHslF(max(ah, 0.0), as_, al)
        values["GREEN_NEON"] = values["GREEN_TEXT"] = accent.name()
        # Survol des boutons principaux : dérivé de l'accent (le texte sombre
        # reste lisible), et non de la teinte voisine de la palette verte
        values["GREEN_BRIGHT"] = _mix(accent, QColor("#ffffff"), HOVER_TOWARD_WHITE).name()
        if contrast(accent, QColor(values["ON_ACCENT"])) < 4.5:   # « FX » du badge allumé
            values["ON_ACCENT"] = values["ON_ACCENT_DARK"]
    globals().update(values)
    _current_theme = theme
    return theme


def current_theme() -> str:
    return _current_theme


def _rgba(value: str, alpha: float) -> str:
    c = QColor(value)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {max(0.0, min(1.0, alpha)):.3f})"


def build_stylesheet(panel_opacity: float = 1.0) -> str:
    """QSS de l'application, aux couleurs du thème en cours. `panel_opacity`
    < 1 laisse voir l'arrière-plan (image personnalisée) à travers les
    colonnes et leurs blocs."""
    return (_stylesheet()
            .replace("@COLUMN_BG@", _rgba(BG_COLUMN, panel_opacity))
            .replace("@SECTION_BG@", _rgba(BG_SECTION, panel_opacity)))


def refresh(app: QApplication, panel_opacity: float = 1.0) -> None:
    """Ré-applique palette et QSS (après un changement de thème ou d'opacité)."""
    _apply_palette(app)
    stylesheet = build_stylesheet(panel_opacity)
    if app.styleSheet() != stylesheet:   # re-styler toute l'app coûte : seulement si besoin
        app.setStyleSheet(stylesheet)


def set_panel_opacity(app: QApplication, panel_opacity: float) -> None:
    refresh(app, panel_opacity)


def apply_theme(app: QApplication, panel_opacity: float = 1.0, theme: str | None = None) -> None:
    """Charge les polices et applique thème, palette et QSS à toute l'application."""
    load_fonts()
    app.setFont(font(11))
    set_theme(theme)
    _apply_palette(app)
    app.setStyleSheet(build_stylesheet(panel_opacity))


def _apply_palette(app: QApplication) -> None:
    palette = app.palette()
    for role, value in (
        (QPalette.ColorRole.Window, BG_WINDOW),
        (QPalette.ColorRole.Base, BG_SECTION),
        (QPalette.ColorRole.AlternateBase, BG_COLUMN),
        (QPalette.ColorRole.WindowText, TEXT_PRIMARY),
        (QPalette.ColorRole.Text, TEXT_PRIMARY),
        (QPalette.ColorRole.ButtonText, TEXT_PRIMARY),
        (QPalette.ColorRole.Button, BUTTON_BG),
        (QPalette.ColorRole.Highlight, GREEN_DIM),
        (QPalette.ColorRole.HighlightedText, WHITE),
        (QPalette.ColorRole.ToolTipBase, BG_COLUMN),
        (QPalette.ColorRole.ToolTipText, TEXT_PRIMARY),
        (QPalette.ColorRole.PlaceholderText, TEXT_MUTED),
    ):
        palette.setColor(role, QColor(value))
    app.setPalette(palette)


def _stylesheet() -> str:
    return f"""
* {{
    font-family: "{FONT_FAMILY}", "{FONT_FALLBACK}";
    outline: none;
}}
QWidget {{
    color: {TEXT_PRIMARY};
    font-size: 11px;
}}
QMainWindow, QDialog {{
    background: {BG_WINDOW};
}}

/* ---- colonnes et blocs ---- */
QFrame#column {{
    background: @COLUMN_BG@;
    border: 1px solid {BORDER_COLUMN};
    border-radius: 14px;
}}
QFrame#section {{
    background: @SECTION_BG@;
    border: 1px solid {BORDER_SECTION};
    border-radius: 10px;
}}
QLabel {{
    background: transparent;
}}
QLabel#sectionTitle {{
    color: {TEXT_SECONDARY};
    font-size: 11px;
    font-weight: 600;
}}
QLabel#knobLabel {{
    color: {TEXT_SECONDARY};
    font-size: 10px;
    font-weight: 500;
}}
QLabel#faderValue {{
    color: {TEXT_PRIMARY};
    font-size: 11px;
    font-weight: 600;
}}
QLabel#faderValue[muted="true"] {{
    color: {TEXT_MUTED};
}}
QLabel#stripName {{
    color: {TEXT_PRIMARY};
    font-size: 12px;
    font-weight: 600;
}}
QLineEdit#stripNameEdit {{
    background: {BG_SECTION};
    border: 1px solid {GREEN_DIM};
    border-radius: 5px;
    color: {TEXT_PRIMARY};
    font-size: 12px;
    font-weight: 600;
    padding: 1px 4px;
    selection-background-color: {GREEN_DIM};
}}
QLabel#masterTitle {{
    color: {TEXT_PRIMARY};
    font-size: 18px;
    font-weight: 600;
}}
QLabel#masterValue {{
    color: {TEXT_PRIMARY};
    font-size: 14px;
    font-weight: 600;
}}
QLabel#masterLabel {{
    color: {TEXT_SECONDARY};
    font-size: 11px;
    font-weight: 500;
}}

/* ---- bouton Muet ---- */
QPushButton#muteButton {{
    background: {BUTTON_BG};
    border: 1px solid {BORDER_COLUMN};
    border-radius: 7px;
    color: {TEXT_PRIMARY};
    font-size: 11px;
    font-weight: 600;
}}
QPushButton#muteButton:hover {{
    background: {BUTTON_HOVER};
    border-color: {GREEN_DIM};
}}
QPushButton#muteButton:checked {{
    background: {RED_MUTE};
    border: none;
    color: {TEXT_ON_RED};
}}
QPushButton#muteButton:checked:hover {{
    background: #ff4545;
}}

/* ---- zone d'effets ---- */
QScrollArea#fxScroll, QScrollArea#fxScroll > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 4px;
    margin: 2px 0;
}}
QScrollBar::handle:vertical {{
    background: {GREEN_DIM};
    border-radius: 2px;
    min-height: 20px;
}}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
    height: 0;
}}

/* ---- dialogues ---- */
QDialog QLabel#dialogTitle {{
    color: {TEXT_PRIMARY};
    font-size: 15px;
    font-weight: 600;
}}
QDialog QLabel#hint {{
    color: {TEXT_SECONDARY};
}}
QDialog QLabel#warning {{
    color: {YELLOW_VU};
}}
QDialog QLabel#ok {{
    color: {GREEN_NEON};
}}
QGroupBox {{
    background: {BG_COLUMN};
    border: 1px solid {BORDER_COLUMN};
    border-radius: 10px;
    margin-top: 18px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: {TEXT_SECONDARY};
}}
QLineEdit, QComboBox {{
    background: {BG_SECTION};
    border: 1px solid {BORDER_COLUMN};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {GREEN_DIM};
}}
QLineEdit:focus, QComboBox:focus {{
    border-color: {GREEN_DIM};
}}
QComboBox QAbstractItemView {{
    background: {BG_COLUMN};
    border: 1px solid {BORDER_COLUMN};
    selection-background-color: {GREEN_DIM};
}}
QListWidget {{
    background: {BG_SECTION};
    border: 1px solid {BORDER_COLUMN};
    border-radius: 8px;
    padding: 4px;
}}
QListWidget::item {{
    padding: 5px 6px;
    border-radius: 5px;
}}
QListWidget::item:hover {{
    background: {BUTTON_BG};
}}
QListWidget::item:selected {{
    background: {GREEN_DIM};
    color: {WHITE};
}}
QPushButton {{
    background: {BUTTON_BG};
    border: 1px solid {BORDER_COLUMN};
    border-radius: 7px;
    padding: 6px 14px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {BUTTON_HOVER};
    border-color: {GREEN_DIM};
}}
QPushButton:pressed {{
    background: {GREEN_DIM};
}}
QPushButton:disabled {{
    color: {TEXT_MUTED};
}}
QPushButton#primary {{
    background: {GREEN_NEON};
    border: none;
    color: {ON_ACCENT_DARK};
}}
QPushButton#primary:hover {{
    background: {GREEN_BRIGHT};
}}
QPushButton#primary:disabled {{
    background: {GREEN_DIM};
    color: {TEXT_MUTED};
}}
QCheckBox {{
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {GREEN_DIM};
    background: {BG_SECTION};
}}
QCheckBox::indicator:checked {{
    background: {GREEN_NEON};
    border-color: {GREEN_NEON};
}}
QMenu {{
    background: {BG_COLUMN};
    border: 1px solid {BORDER_COLUMN};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 18px 6px 12px;
    border-radius: 5px;
}}
QMenu::item:selected {{
    background: {GREEN_DIM};
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER_COLUMN};
    margin: 4px 6px;
}}
QToolTip {{
    background: {BG_COLUMN};
    color: {TEXT_PRIMARY};
    border: 1px solid {GREEN_DIM};
    border-radius: 4px;
    padding: 4px 6px;
}}
QMessageBox {{
    background: {BG_WINDOW};
}}

/* ---- onglets, curseurs, choix (réglages) ---- */
QTabWidget::pane {{
    border: none;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_SECONDARY};
    padding: 7px 14px;
    margin-right: 4px;
    border: 1px solid transparent;
    border-radius: 7px;
    font-weight: 600;
}}
QTabBar::tab:hover {{
    color: {TEXT_PRIMARY};
    background: {BUTTON_BG};
}}
QTabBar::tab:selected {{
    color: {TEXT_PRIMARY};
    background: {BUTTON_BG};
    border-color: {GREEN_DIM};
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {BUTTON_BG};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {GREEN_DIM};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {GREEN_NEON};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QRadioButton {{
    spacing: 8px;
}}
QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border-radius: 7px;
    border: 1px solid {GREEN_DIM};
    background: {BG_SECTION};
}}
QRadioButton::indicator:checked {{
    background: {GREEN_NEON};
    border-color: {GREEN_NEON};
}}
QLabel#credits {{
    color: {TEXT_SECONDARY};
}}
"""
