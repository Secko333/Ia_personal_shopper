"""Costanti globali e configurazione centralizzata."""

from pathlib import Path

# Directory dati utente (~/.config/ia_shopper/)
CONFIG_DIR = Path.home() / ".config" / "ia_shopper"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

PROFILO_PATH = CONFIG_DIR / "profilo.json"
PREFERITI_PATH = CONFIG_DIR / "preferiti.json"
GUARDAROBA_PATH = CONFIG_DIR / "guardaroba.json"
BROWSER_DATA_DIR = CONFIG_DIR / "browser"
BROWSER_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Modelli Claude — un modello per ruolo, il più economico che regge il compito:
# navigazione browser = Sonnet ($3/$15 per MTok, Opus non serve),
# vision/valutazione = Haiku ($1/$5, supporta vision; task semplici e ben delimitati).
MODELLO_BROWSER = "claude-sonnet-4-6"
MODELLO_VISION = "claude-haiku-4-5"
MODELLO_VALUTAZIONE = "claude-haiku-4-5"

# Siti supportati e loro domini
SITI_SUPPORTATI = ["zalando", "zara", "vinted"]
DOMINI_SITI = {
    "zalando": "www.zalando.it",
    "zara": "www.zara.com",
    "vinted": "www.vinted.it",
}

# Limiti agente
MAX_STEPS_RICERCA = 25
MAX_RISULTATI_PER_SITO = 12
MAX_RISULTATI_TOTALI = 20

# Ranking per misure (vedi valutazione/fit.py): si pesca largo e si tengono i capi
# più centrati sulle misure target. Il costo dominante è l'arricchimento delle
# descrizioni in vinted_api (1 GET + 0.3s per capo): 60 candidati ≈ 22s per ricerca.
# Se le ricerche diventano troppo lente, questo è il numero da abbassare.
MAX_CANDIDATI_FIT = 60
MAX_RISULTATI_FINALI = 12

# Fallback vision: quanti capi senza misure nella descrizione mandare a leggere dalle foto.
MAX_CAPI_VISION = 6

# Delay iniziale tra lanci agenti (anti-bot, in secondi)
DELAY_MIN_AGENTE = 0.5
DELAY_MAX_AGENTE = 2.0
