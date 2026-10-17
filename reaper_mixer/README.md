<p align="center"><img src="assets/reka_logo.png" alt="REKA" width="420"></p>

# REKA

Console de mixage unique pour Windows : elle pilote **Voicemeeter Potato ou Banana**
(routing, volumes, mutes, VU-mètres) et **REAPER** (effets VST par strip), qui tournent
tous les deux cachés en arrière-plan. L'utilisateur ne voit que la console.

Développé par **Regrettable** et **Zerka**.

![console](https://github.com/regret-py/rekav2/releases/download/build-claude-charming-hawking-bll4o5/console.png)

## Télécharger l'exécutable

Chaque push construit automatiquement `REKA.exe` sur une machine Windows de GitHub
(onglet **Actions**, workflow *Build Windows*) et le publie dans une release
**build-&lt;branche&gt;** (onglet **Releases**). Rien à installer pour le compiler.

## Prérequis

- Windows 10/11 64 bits
- [Voicemeeter Potato](https://vb-audio.com/Voicemeeter/potato.htm) ou
  [Voicemeeter Banana](https://vb-audio.com/Voicemeeter/banana.htm)
- [REAPER](https://www.reaper.fm/download.php) (6.37 ou plus récent conseillé)

Si l'un des deux manque, l'app affiche un lien de téléchargement, ou permet de
localiser l'exécutable s'il est installé ailleurs.

## Premier lancement : faire passer le son par les effets

Les effets s'appliquent grâce à l'**insert** de Voicemeeter : chaque strip envoie son son
à REAPER, qui le renvoie traité. Une seule chose à régler, une fois :

1. Lance REKA.
2. Clique sur ⚙ (panneau Master) → **Ouvrir les préférences audio de REAPER**.
3. Dans REAPER : *Audio system* = **ASIO**, pilote = **Voicemeeter Insert Virtual ASIO**
   (« Voicemeeter Potato Insert Virtual ASIO » selon la version). Valide, puis ferme les
   réglages : la fenêtre de REAPER se cache de nouveau.

L'app active alors l'insert des strips qui ont une piste REAPER (et le remet comme avant
en partant). Tant que REAPER n'est pas sur ce pilote, l'insert n'est pas activé : le son
passe sans effets plutôt que d'être coupé.

## Projet REAPER

Au premier lancement, REKA ouvre un **projet vierge** : une piste câblée par strip,
aucun effet. Pour ouvrir ton propre projet (ton traitement, tes VST) :

- clique sur le bouton **Projet** du panneau Master (ou ⚙ → *Changer de projet…*) ;
- choisis un projet récent ou **Parcourir…** un `.rpp` ;
- coche **Ouvrir ce projet automatiquement au démarrage** pour qu'il devienne le projet
  par défaut ; coche **Me demander quel projet ouvrir à chaque démarrage** si tu veux
  choisir à chaque lancement.

Le projet s'ouvre tout de suite dans REAPER, sans redémarrer. **Son câblage n'est jamais
modifié** : chaque strip affiche la piste du projet qui écoute ses canaux Voicemeeter
(par exemple une piste dont l'entrée est « Input 1 » ou « Input 1 / Input 2 » va sur
Microphone). Si une strip n'a pas de piste, ajouter un effet dessus en crée une, câblée
sur son insert (désactivable dans ⚙ → Options).

## Utilisation

| Élément | Action |
|---|---|
| Fader | glisser (Maj = fin), molette, double-clic = 0 dB |
| Knobs Monitor / Mic Out / Discord / Obs | clic = envoi on/off vers A1 / B1 / B2 / A2, glisser = niveau d'envoi |
| Intellipan (carré au petit cube rouge) | glisser le cube = régler, double-clic = neutre, clic sur le titre ou clic droit = mode |
| Bouton Apps (strips virtuelles) | volume, mute et niveau de chaque application qui joue sur la strip |
| Pill « Mise à jour » (Master) | apparaît quand une nouvelle version est en ligne ; clic pour l'installer |
| Muet | coupe la strip |
| Pill d'effet | clic = ouvrir/fermer l'interface du plugin, clic droit = bypass / supprimer |
| Emplacement vide (« + ») | ajouter un effet (recherche dans les plugins connus de REAPER) |
| Badge FX ⏻ | bypass de toute la chaîne d'effets de la strip |
| Nom de la strip | double-clic pour renommer (renomme aussi la piste REAPER) |
| Knob Casque | volume du bus A1 (double-clic = 0 dB) |
| Badge de connexion | vert = tout répond ; rouge = REAPER ou Voicemeeter ne répond plus |
| Bouton Projet | projet REAPER ouvert ; clic pour en changer |
| Bouton – (Master) | réduire REKA (barre des tâches, ou zone de notification si activé) |
| Bouton ⚙ (Master) | réglages : Général, Audio, Apparence, À propos |

`REKA.exe --demo` ouvre la console avec des moteurs simulés (sans Voicemeeter ni REAPER)
pour la découvrir.

## Intellipan

Comme dans Voicemeeter, chaque strip a son **Intellipan** : un carré où l'on déplace un
petit cube rouge.

- **Entrées matérielles** (Microphone, Musique, Autres 1) : 3 modes, choisis en cliquant
  sur le titre du carré (ou clic droit) — **VOIX** (couleur du son, le « Color Panel » de
  Voicemeeter), **POSITION** (panoramique) et **EFFETS** (écho / modulation).
- **Entrées virtuelles** (Appels, Bureau, Autres 2) : **POSITION** (panoramique 5.1).

Le mode choisi est retenu pour chaque strip. Double-clic = retour au neutre.

## Volume par application

Sur les strips virtuelles (Appels, Bureau, Autres 2 — les entrées « Voicemeeter Input »,
« Voicemeeter AUX Input », « Voicemeeter VAIO3 Input » de Windows), le bouton **Apps** à
côté de **Muet** affiche le nombre d'applications qui y jouent. Un clic ouvre la liste :
pour chaque application (Discord, Spotify, navigateur, jeu…), son volume, son niveau en
direct et un bouton pour la couper. Ce sont les réglages Windows de l'application (les
mêmes que dans le mélangeur de volume de Windows) : ils sont conservés par Windows.

Pour qu'une application apparaisse sur une strip, choisis la sortie Voicemeeter
correspondante dans l'application ou dans Windows (Paramètres › Son › Mélangeur de volume).
Si tes périphériques ont d'autres noms, indique-les dans `config.json`
(`voicemeeter.app_devices`, un morceau de nom par entrée virtuelle).

## Mises à jour

REKA vérifie au démarrage (puis toutes les 6 heures) si une nouvelle version est en ligne.
Si oui, une pill **Mise à jour** apparaît dans le panneau Master :

- **Mettre à jour** : REKA télécharge le nouvel exe, vérifie qu'il est complet (empreinte
  SHA-256), le met à la place de l'ancien puis redémarre dessus. Réglages et projet REAPER
  sont conservés.
- **Navigateur** : télécharge REKA.exe à la main ; **Plus tard** ; **Ignorer cette
  version** (REKA ne la propose plus, sauf recherche manuelle).

⚙ → **À propos** : version et numéro de build, **Rechercher une mise à jour**, et l'option
**Vérifier les mises à jour au démarrage**. Chaque build GitHub publie, à côté de
`REKA.exe`, un `version.json` (numéro de build, taille, empreinte) que REKA consulte.

## Personnaliser l'interface

⚙ → onglet **Apparence** :

- **Thème de couleur** : vert néon, cyan, bleu, violet, rose, rouge, orange, or, blanc, ou
  la pastille arc-en-ciel pour n'importe quelle couleur. Toute l'interface change tout de
  suite (knobs, VU-mètres, boutons, fonds teintés) ; le rouge du Muet et du petit cube
  reste rouge.
- **Arrière-plan** : *Choisir une image…* (PNG, JPG, WebP…). L'image est copiée dans
  `%APPDATA%\REKA`, cadrée pour remplir la fenêtre, et peut être assombrie (curseur
  *Assombrir l'image*). *Fond d'origine* revient au fond REKA.
- **Opacité des panneaux** : baisse-la pour voir l'image à travers les strips.
- **Fenêtre** : REKA s'ouvre en fenêtre compacte, comme Voicemeeter (juste les strips et
  le Master) ; sa taille et sa position sont mémorisées. *Taille compacte* la remet à sa
  taille d'origine, centrée. Coche *Réduire dans la zone de notification* pour que le
  bouton **–** l'envoie à côté de l'horloge (le son continue ; clic sur l'icône REKA pour
  la rouvrir, clic droit → *Quitter REKA*).

## Voicemeeter Potato ou Banana

⚙ → onglet **Général** → *Édition* : choisis **Potato** (8 entrées, 6 strips) ou
**Banana** (5 entrées, 5 strips), puis **Redémarrer REKA**. REKA lance alors la bonne
édition et ouvre un projet vierge adapté (`session-banana.rpp` pour Banana). Ferme l'autre
édition de Voicemeeter si elle tourne encore. Sur Banana, les knobs d'envoi sont de simples
interrupteurs on/off (Banana n'a pas de niveaux d'envoi par bus).

## Correspondance

| Strip | Potato | Banana |
|---|---|---|
| Microphone | Strip 0 (entrée matérielle 1) — canaux 1/2 | Strip 0 — canaux 1/2 |
| Musique | Strip 1 (entrée matérielle 2) — canaux 3/4 | Strip 1 — canaux 3/4 |
| Appels | Strip 5 (entrée virtuelle 1) — canaux 11/12 | Strip 3 (VAIO) — canaux 7/8 |
| Bureau | Strip 6 (entrée virtuelle 2) — canaux 19/20 | Strip 4 (AUX) — canaux 15/16 |
| Autres 1 | Strip 2 (entrée matérielle 3) — canaux 5/6 | Strip 2 — canaux 5/6 |
| Autres 2 | Strip 7 (entrée virtuelle 3) — canaux 27/28 | — |

Les canaux sont ceux de l'insert Voicemeeter (entrée de la piste REAPER). Modifiable dans
`config.json` (`voicemeeter.strip_maps.potato` / `.banana`, `voicemeeter.routing`).

## Fonctionnement

- **Voicemeeter** : API Remote via `voicemeeterlib`. Un thread lit paramètres et niveaux
  à 30 Hz ; les réglages de l'UI sont envoyés sans bloquer l'interface.
- **REAPER** : un petit script Lua (`assets/reka_bridge.lua`) tourne dans REAPER et
  échange avec l'app par fichiers texte dans `%APPDATA%\REKA\bridge`. Il utilise
  directement l'API de REAPER (ajout d'un plugin par son nom, suppression, bypass,
  ouverture des interfaces, ouverture de projet, sauvegarde). L'OSC natif de REAPER ne
  sait pas faire tout ça et demanderait un réglage manuel.
- Le pont est chargé par un bloc ajouté en tête de `Scripts\__startup.lua` de REAPER. Ce
  bloc ne fait rien quand tu lances REAPER toi-même : il ne s'active que si REAPER est
  lancé par REKA.
- Le projet ouvert est sauvegardé à chaque changement d'effet, toutes les 30 s s'il a
  été modifié, et à la fermeture.
- Fenêtres : Voicemeeter et REAPER sont masqués (et re-masqués s'ils réapparaissent).
  Les interfaces des plugins restent visibles et passent au premier plan.
- À la fermeture : sauvegarde du projet, inserts rendus, arrêt de REAPER, puis extinction
  propre de Voicemeeter (`Command.Shutdown`). Un Voicemeeter déjà ouvert avant l'app
  n'est pas fermé : il est simplement ré-affiché.

Relancer `REKA.exe` alors que REKA tourne déjà (par exemple cachée près de l'horloge)
ré-affiche simplement la fenêtre.

Si une autre édition de Voicemeeter est ouverte au démarrage, ou si seule l'autre édition
est installée, REKA propose de l'utiliser directement.

## Fichiers

```
%APPDATA%\REKA\
  config.json      réglages (copie modifiable du config.json livré)
  app.log          journal (3 × 2 Mo)
  session.rpp      projet vierge de REKA (session-banana.rpp pour Banana)
  background.*     image d'arrière-plan choisie
  processes.json   processus lancés par l'app (reprise après plantage)
  bridge\          échanges avec le pont Lua
```

Les données de l'ancien dossier `%APPDATA%\REAPER Mixer` sont reprises automatiquement.

## Dépannage

- **Journal** : ⚙ → *Ouvrir le dossier des journaux* (`app.log`). Mets `"log_level": "DEBUG"`
  dans `config.json` pour plus de détails.
- **« REAPER ne répond pas » au démarrage** : au tout premier lancement, REAPER analyse tes
  plugins, ce qui peut prendre une minute : clique sur *Réessayer*. Vérifie aussi qu'aucune
  fenêtre de REAPER (licence d'évaluation…) n'attend une réponse.
- **Pas d'effet sur le son** : vérifie la carte son de REAPER dans ⚙ (doit indiquer
  « Voicemeeter Insert Virtual ASIO »), et que la piste de la strip est armée en écoute
  (sinon le badge FX l'indique au survol). Si REAPER affiche « [audio device closed] »,
  décoche dans ses préférences *Audio → Close audio device when stopped and application
  is inactive*.
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
python -m PyInstaller --noconfirm --clean build.spec   # → dist\REKA.exe
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
│   ├── config.py           config.json (défauts + %APPDATA%\REKA), chemins
│   ├── process_manager.py  moteurs (recherche, lancement, masquage, arrêt), projet REAPER
│   ├── voicemeeter_ctrl.py faders, mutes, routing, envois, insert, VU (30 Hz)
│   ├── reaper_ctrl.py      liaison avec le pont Lua (effets, pistes, projet, carte son)
│   ├── vst_scanner.py      liste des plugins (REAPER, cache, dossiers VST3)
│   └── demo.py             moteurs simulés (--demo, --selftest)
├── ui/                     fenêtre, strips, master, knob/fader/VU dessinés, dialogues
├── assets/                 logo et icône REKA, police Inter, pont Lua
├── tests/                  tests (logique, pont Lua, interface, Win32)
├── config.json
├── requirements.txt
└── build.spec
```
