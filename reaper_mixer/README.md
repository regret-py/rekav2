# REAPER Mixer

Console de mixage unique pour Windows : elle pilote **Voicemeeter Potato** (routing,
volumes, mutes, VU-mètres) et **REAPER** (effets VST par strip), qui tournent tous les
deux cachés en arrière-plan. L'utilisateur ne voit que la console.

![console](https://github.com/regret-py/rekav2/releases/download/build-claude-charming-hawking-bll4o5/console.png)

## Télécharger l'exécutable

Chaque push construit automatiquement `REAPER-Mixer.exe` sur une machine Windows de
GitHub (onglet **Actions**, workflow *Build Windows*) et le publie dans une release
**build-&lt;branche&gt;** (onglet **Releases**). Rien à installer pour le compiler.

## Prérequis

- Windows 10/11 64 bits
- [Voicemeeter Potato](https://vb-audio.com/Voicemeeter/potato.htm)
- [REAPER](https://www.reaper.fm/download.php) (6.37 ou plus récent conseillé)

Si l'un des deux manque, l'app affiche un lien de téléchargement, ou permet de
localiser l'exécutable s'il est installé ailleurs.

## Premier lancement : faire passer le son par les effets

Les effets s'appliquent grâce à l'**insert** de Voicemeeter : chaque strip envoie son son
à REAPER, qui le renvoie traité. Une seule chose à régler, une fois :

1. Lance REAPER Mixer.
2. Clique sur ⚙ (panneau Master) → **Ouvrir les préférences audio de REAPER**.
3. Dans REAPER : *Audio system* = **ASIO**, pilote = **Voicemeeter Insert Virtual ASIO**.
   Valide, puis ferme les réglages : la fenêtre de REAPER se cache de nouveau.

L'app active alors l'insert des 6 strips toute seule (et le remet comme avant en partant).
Tant que REAPER n'est pas sur ce pilote, l'insert n'est pas activé : le son passe sans
effets plutôt que d'être coupé.

## Utilisation

| Élément | Action |
|---|---|
| Fader | glisser (Maj = fin), molette, double-clic = 0 dB |
| Knobs Monitor / Mic Out / Discord / Obs | clic = envoi on/off vers A1 / B1 / B2 / A2, glisser = niveau d'envoi |
| Muet | coupe la strip |
| Pill d'effet | clic = ouvrir/fermer l'interface du plugin, clic droit = bypass / supprimer |
| Emplacement vide (« + ») | ajouter un effet (recherche dans les plugins connus de REAPER) |
| Badge FX ⏻ | bypass de toute la chaîne d'effets de la strip |
| Nom de la strip | double-clic pour renommer (renomme aussi la piste REAPER) |
| Knob Casque | volume du bus A1 (double-clic = 0 dB) |
| Badge de connexion | vert = tout répond ; rouge = REAPER ou Voicemeeter ne répond plus |

`REAPER-Mixer.exe --demo` ouvre la console avec des moteurs simulés (sans Voicemeeter ni
REAPER) pour la découvrir.

## Correspondance

| Strip | Voicemeeter | Piste REAPER | Canaux de l'insert |
|---|---|---|---|
| Microphone | Strip 0 (entrée matérielle 1) | 1 | 1/2 |
| Musique | Strip 1 (entrée matérielle 2) | 2 | 3/4 |
| Appels | Strip 5 (entrée virtuelle 1) | 3 | 11/12 |
| Bureau | Strip 6 (entrée virtuelle 2) | 4 | 19/20 |
| Autres 1 | Strip 2 (entrée matérielle 3) | 5 | 5/6 |
| Autres 2 | Strip 7 (entrée virtuelle 3) | 6 | 27/28 |

Modifiable dans `config.json` (`voicemeeter.strip_map`, `voicemeeter.routing`).

## Fonctionnement

- **Voicemeeter** : API Remote via `voicemeeterlib`. Un thread lit paramètres et niveaux
  à 30 Hz ; les réglages de l'UI sont envoyés sans bloquer l'interface.
- **REAPER** : un petit script Lua (`assets/reaper_mixer_bridge.lua`) tourne dans REAPER et
  échange avec l'app par fichiers texte dans `%APPDATA%\REAPER Mixer\bridge`. Il utilise
  directement l'API de REAPER (ajout d'un plugin par son nom, suppression, bypass,
  ouverture des interfaces, sauvegarde). L'OSC natif de REAPER ne sait pas faire tout ça
  et demanderait un réglage manuel.
- Le pont est chargé par un bloc ajouté en tête de `Scripts\__startup.lua` de REAPER. Ce
  bloc ne fait rien quand tu lances REAPER toi-même : il ne s'active que si REAPER est
  lancé par l'app.
- Le projet REAPER est conservé dans `%APPDATA%\REAPER Mixer\session.rpp` (créé depuis
  `assets/reaper_template.rpp`) : tes chaînes d'effets et leurs réglages sont gardés
  d'un lancement à l'autre (sauvegarde à chaque changement + toutes les 30 s).
- Fenêtres : Voicemeeter et REAPER sont masqués (et re-masqués s'ils réapparaissent).
  Les interfaces des plugins restent visibles et passent au premier plan.
- À la fermeture : sauvegarde du projet, extinction propre de Voicemeeter
  (`Command.Shutdown`), puis arrêt de REAPER. Un Voicemeeter déjà ouvert avant l'app
  n'est pas fermé : il est simplement ré-affiché.

## Fichiers

```
%APPDATA%\REAPER Mixer\
  config.json      réglages (copie modifiable du config.json livré)
  app.log          journal (3 × 2 Mo)
  session.rpp      projet REAPER de la console
  processes.json   processus lancés par l'app (reprise après plantage)
  bridge\          échanges avec le pont Lua
```

## Dépannage

- **Journal** : ⚙ → *Ouvrir le dossier des journaux* (`app.log`). Mets `"log_level": "DEBUG"`
  dans `config.json` pour plus de détails.
- **« REAPER ne répond pas » au démarrage** : au tout premier lancement, REAPER analyse tes
  plugins, ce qui peut prendre une minute : clique sur *Réessayer*. Vérifie aussi qu'aucune
  fenêtre de REAPER (licence d'évaluation…) n'attend une réponse.
- **Pas d'effet sur le son** : vérifie la carte son de REAPER dans ⚙ (doit indiquer
  « Voicemeeter Insert Virtual ASIO »).
- **Voir Voicemeeter ou REAPER** : ⚙ → cases *Afficher la fenêtre…* (re-masquées à la
  fermeture des réglages).

## Développement

Python 3.11+ (testé en 3.12 et 3.13).

```
cd reaper_mixer
python -m pip install -r requirements.txt pytest
python main.py            # la vraie console
python main.py --demo     # moteurs simulés
python -m pytest tests    # tests (le pont Lua est testé si lua5.4 est installé)
python -m PyInstaller --noconfirm --clean build.spec   # → dist\REAPER Mixer.exe
```

Avec le Python du Microsoft Store, `pip` peut échouer sur des chemins trop longs : active
les chemins longs (PowerShell administrateur, puis rouvre le terminal) :

```
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force
```

Arborescence :

```
reaper_mixer/
├── main.py                 démarrage, écran de chargement, popups, arrêt
├── core/
│   ├── config.py           config.json (défauts + %APPDATA%), chemins
│   ├── process_manager.py  recherche, lancement, masquage, arrêt des moteurs
│   ├── voicemeeter_ctrl.py faders, mutes, routing, envois, insert, VU (30 Hz)
│   ├── reaper_ctrl.py      liaison avec le pont Lua (effets, pistes, carte son)
│   ├── vst_scanner.py      liste des plugins (REAPER, cache, dossiers VST3)
│   └── demo.py             moteurs simulés (--demo, --selftest)
├── ui/                     fenêtre, strips, master, knob/fader/VU dessinés, dialogues
├── assets/                 police Inter, icône, template REAPER, pont Lua
├── tests/                  tests (logique, pont Lua, interface, Win32)
├── config.json
├── requirements.txt
└── build.spec
```
