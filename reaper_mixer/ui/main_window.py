"""
Fenêtre principale : 6 strips à gauche, panneau master à droite.

Étape 5. Contrat utilisé par main.py :
    MainWindow(cfg=..., vm=..., reaper=..., processes=...)
    closeEvent écrit l'état (faders, casque) dans cfg["state"] ; main.py sauvegarde.
"""
