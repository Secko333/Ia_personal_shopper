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

# Due numeri diversi perché costano in modo diverso.
# I candidati GREZZI arrivano dalla ricerca JSON: 4 richieste in tutto, praticamente gratis.
# I candidati FIT sono quelli di cui si legge la pagina per estrarne le misure, e lì Vinted
# ha un tetto misurato (2026-08-31): ~30 pagine di fila passano, dalla 31esima in poi
# risponde 429 comunque, a qualunque parallelismo. Leggerne 60 non dava 60 descrizioni,
# dava 30 descrizioni e 30 capi che sembravano senza misure.
# Quindi: si pesca largo e si legge stretto — fra i 120 grezzi si leggono solo i 30 più
# vicini ai gusti dell'utente (vedi ricerca/coordinatore._migliori_per_gusto).
# Alzare MAX_CANDIDATI_FIT non porta più capi: porta solo più 429.
MAX_CANDIDATI_GREZZI = 120
MAX_CANDIDATI_FIT = 30
MAX_RISULTATI_FINALI = 12

# Di quei grezzi, quanti pescati aggiungendo un termine di stile — divisi tra i termini, una
# ricerca per ciascuno (vedi ricerca/coordinatore._cerca_vinted). Il resto va alla ricerca
# pura sulle misure. Metà e metà: le misure restano il criterio che scarta, ma un capo che
# veste bene e non è nei gusti dell'utente non serve a niente.
MAX_CANDIDATI_STILE = 60

# Quante pagine prodotto si leggono insieme per estrarne descrizione e foto: è il grosso
# dell'attesa di una ricerca. Misurato il 2026-08-31 su 30 pagine di fila: a 2 tornano
# tutte e 30 in 20s, a 3 Vinted risponde 429 a tutte e 30. Il limite non è la nostra
# banda ma il suo rate limit, ed è ripido: 2 è il tetto, alzarlo non velocizza — fa
# tornare i capi senza descrizione, che è come se il venditore non avesse scritto le misure.
DESCRIZIONI_PARALLELE = 2

# Delay iniziale tra lanci agenti (anti-bot, in secondi)
DELAY_MIN_AGENTE = 0.5
DELAY_MAX_AGENTE = 2.0
