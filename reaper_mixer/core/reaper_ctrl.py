"""
Pilotage de REAPER en OSC (python-osc) : liste des FX par track, ajout,
suppression, bypass, ouverture de l'UI d'un plugin.

Étape 2. Contrat utilisé par main.py :
    ReaperController(cfg)               # non bloquant, créé dans le thread UI
    .start()                            # démarre l'écoute OSC ; idempotent
    .ping(timeout_s: float) -> bool     # True si REAPER a répondu
    .close(save_project: bool)          # sauvegarde le projet de session, arrête l'écoute
"""
