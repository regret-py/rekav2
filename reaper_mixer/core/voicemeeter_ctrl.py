"""
Contrôle de Voicemeeter Potato via l'API Remote (voicemeeterlib).
Faders, mutes, routing A1/A2/B1/B2, VU-mètres (polling 30 Hz → signaux Qt).

Étape 2. Contrat utilisé par main.py :
    VoicemeeterController(cfg)          # non bloquant, créé dans le thread UI
    .login()                            # bloquant, lève une exception si VM pas prêt ; idempotent
    .close(shutdown_app: bool)          # arrête le polling, éteint VM proprement si demandé
"""
