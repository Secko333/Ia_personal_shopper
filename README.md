# 🛍️ Personal AI Shopper

Un agente AI personale che naviga Zalando, Zara e Vinted per trovare i capi che cerchi — per descrizione o per foto — filtrandoli per budget e valutandoli in base al tuo fisico e al tuo stile.

---

## Come funziona

### Il motore: browser-use + Claude

**Vinted** (la piattaforma principale) usa l'**API JSON interna** via `curl_cffi` con fingerprint Chrome: veloce, gratuita (zero token LLM), con filtri su budget e taglie del profilo.

**Zalando e Zara** usano **browser-use**, un framework che fa guidare il browser a Claude come farebbe un umano reale: vede la pagina, capisce la UI, clicca, digita nelle barre di ricerca, scorre i risultati. Molto più robusto ai cambiamenti di layout rispetto allo scraping CSS.

Le ricerche sui siti attivi partono **in parallelo**; i risultati vengono aggregati, deduplicati e ordinati per prezzo.

### Il flusso di una ricerca

```
Tu scrivi: "Cerca una giacca di pelle marrone, max 80€"
    │
    ├─► Estrazione budget (regex): 80€
    │
    ├─► Ricerche parallele (asyncio):
    │       ├── Claude naviga Zalando → cerca → estrae prodotti (JSON strutturato)
    │       ├── Claude naviga Zara    → cerca → estrae prodotti (JSON strutturato)
    │       └── API JSON Vinted       → filtro prezzo + taglie profilo → descrizioni con misure
    │
    ├─► Aggregazione: deduplica per URL, filtra >80€, ordina per prezzo
    │
    └─► Valutazione (singola chiamata Claude batch):
            Per ogni prodotto → si_adatta_fisico? ottimo_affare? commento + raccomandazione
                │
                └─► Tabella Rich con colori: ✅ compra / 🤔 considera / ❌ evita
```

### Il flusso con una foto

```
Tu scrivi: "/foto /Desktop/giacca.jpg budget 60€"
    │
    ├─► Claude Vision analizza l'immagine:
    │       "giacca biker in pelle nera, stile rock/casual, fit slim"
    │
    └─► La descrizione diventa la query → stesso flusso di una ricerca testuale
```

### Le valutazioni

Dopo ogni ricerca, Claude valuta tutti i prodotti trovati in una **singola chiamata batch** usando il tuo profilo fisico:

- **si_adatta_fisico**: considera altezza, circonferenze, tipo di fisico e taglie abituali
- **ottimo_affare**: stima se il prezzo è sotto la media di mercato per quel tipo di capo
- **commento**: max 12 parole, diretto e onesto — se costa troppo lo dice, se la taglia tende piccola lo dice
- **raccomandazione**: `compra` (verde) / `considera` (giallo) / `evita` (rosso)

### Persistenza

- **Profilo utente**: `~/.config/ia_shopper/profilo.json` — misure, taglie, stile, budget default, siti attivi
- **Preferiti**: `~/.config/ia_shopper/preferiti.json` — articoli salvati con query originale e data
- **Cookie browser**: `~/.config/ia_shopper/browser/<sito>/` — sessione persistente per sito, riduce i CAPTCHA nei run successivi

---

## Installazione

### Prerequisiti

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (gestore pacchetti)
- Una chiave API Anthropic

### Setup

```bash
# 1. Clona e vai nella directory
git clone <repo> && cd my_personal_ia_shopper

# 2. Installa le dipendenze
uv sync

# 3. Installa i browser Playwright (solo la prima volta)
uv run playwright install chromium

# 4. Configura la chiave API nel file .env
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
```

---

## Avvio

```bash
uv run shopper
# oppure
uv run python -m Ia_personal_shopper
```

---

## Prima volta: compila il tuo profilo

Al primo avvio il profilo è vuoto. Le valutazioni funzionano meglio se inserisci le tue misure:

```
Tu: /profilo modifica
```

Il wizard ti chiede:
- **Nome**
- **Misure fisiche**: altezza, peso, circonferenze petto/vita/fianchi/collo, lunghezza gamba interna, note corporatura
- **Taglie abituali**: top/maglia, pantaloni, scarpe
- **Stili preferiti**: es. `casual, minimal, streetwear`
- **Budget default**: usato quando non specifichi un budget nella ricerca

---

## Guida ai comandi

### Ricerca testuale

Scrivi liberamente in italiano. Puoi specificare il capo, colore, materiale, stile e budget:

```
Cerca una felpa grigia oversize, max 50€
Voglio dei jeans slim fit neri, entro 60€
Giacca di pelle marrone stile biker
Vestito elegante per cerimonia, max 150€
```

Se non specifichi il budget, viene usato quello del tuo profilo.

### Ricerca per foto

```
/foto /percorso/assoluto/foto.jpg
/foto /percorso/foto.jpg budget 80€
/foto ~/Desktop/giacca.png
```

Formati supportati: `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`

Claude analizza la foto e descrive il capo (tipo, colori, materiale, stile), poi usa quella descrizione per cercare sui siti.

### Leggere i risultati

I risultati appaiono in una tabella con sito, prodotto, prezzo, taglia disponibile e valutazione colorata:

| # | Sito | Prodotto | Prezzo | Taglia | Parere |
|---|------|----------|--------|--------|--------|
| 1 | 🟢 Vinted | Giacca biker / AllSaints | €65 | M | ✅ COMPRA — Ottimo prezzo, M ti starà bene |
| 2 | 🟠 Zalando | Giacca in pelle / Zign | €89 | — | 🤔 CONSIDERA — Buona qualità ma leggermente caro |
| 3 | ⬛ Zara | Giacca effetto pelle / Zara | €99 | — | ❌ EVITA — Ecopelle sottile, non vale il prezzo |

### Salvare un articolo

```
/salva 1        ← salva il prodotto numero 1 tra i preferiti
```

### Aggiungere al carrello

```
/carrello 2     ← apre il browser sulla pagina del prodotto e aggiunge al carrello
```

Il browser rimane aperto con il carrello visibile: puoi procedere manualmente al pagamento.

> **Nota su Vinted**: su Vinted le offerte sono irreversibili. Per sicurezza, `/carrello` su Vinted aggiunge l'articolo ai tuoi **preferiti Vinted** invece di fare un'offerta, così puoi poi decidere con calma.

### Insegnargli i tuoi gusti

```
/stile foto ~/Desktop/outfit.jpg   ← deduce i tuoi tag di stile da una foto di ispirazione
/stile intervista                  ← questionario rapido su vestibilità, colori, occasioni
/mipiace 2                         ← registra che il prodotto 2 ti piace (impara il brand)
/nonmipiace 3                      ← registra cosa evitare in futuro
```

I gusti appresi vengono usati nelle valutazioni successive.

### Gestire il profilo

```
/profilo              ← mostra il tuo profilo completo con misure e taglie
/profilo modifica     ← wizard interattivo per aggiornare tutto
```

### Gestire i siti

```
/siti                 ← mostra quali siti sono attivi/disattivi
                         poi chiede se vuoi attivarne/disattivarne uno
```

### I tuoi preferiti

```
/preferiti            ← tabella di tutti gli articoli salvati
```

### Altri comandi

```
/aiuto                ← lista di tutti i comandi
/esci                 ← esci dall'applicazione
```

---

## Struttura del progetto

```
my_personal_ia_shopper/
├── pyproject.toml
├── .env                             ← ANTHROPIC_API_KEY (non committare!)
└── Ia_personal_shopper/
    ├── __main__.py                  ← entry point (uv run shopper)
    ├── config.py                    ← costanti globali (modelli, percorsi, limiti)
    ├── models.py                    ← tutti i modelli Pydantic
    ├── vinted_api.py                ← client API JSON Vinted (ricerca senza browser)
    ├── cli/
    │   ├── loop.py                  ← REPL: legge input, smista comandi
    │   └── display.py               ← rendering Rich (tabelle, pannelli, colori)
    ├── vision/
    │   └── analizzatore.py          ← foto → base64 → Claude Vision → descrizione
    ├── browser/
    │   ├── agente_base.py           ← factory Agent browser-use
    │   ├── zalando.py               ← task prompt specifico Zalando
    │   ├── zara.py                  ← task prompt specifico Zara
    │   └── vinted.py                ← task preferiti Vinted (la ricerca usa vinted_api)
    ├── ricerca/
    │   ├── coordinatore.py          ← ricerche parallele (API Vinted + agenti browser)
    │   └── aggregatore.py           ← deduplica, filtra per budget, ordina
    ├── valutazione/
    │   └── consulente.py            ← Claude valuta prodotti vs profilo fisico
    └── profilo/
        └── gestore.py               ← load/save atomico JSON profilo + preferiti
```

---

## Note tecniche

### Anti-bot

Il browser si apre **visibile** (non in modalità headless) intenzionalmente:

- I siti riconoscono i browser headless più facilmente
- Puoi risolvere manualmente eventuali CAPTCHA
- Dopo il primo CAPTCHA, i cookie vengono salvati in `~/.config/ia_shopper/browser/<sito>/` e i run successivi di solito non lo richiedono più

### Se un sito non risponde

Se un sito fallisce (CAPTCHA bloccante, timeout, errore server), gli altri due continuano e mostrano i loro risultati normalmente. L'agente segnala quale sito ha avuto problemi con un avviso giallo.

### Modelli Claude usati

| Componente | Modello | Motivo |
|---|---|---|
| Agenti browser | claude-sonnet-4-6 | Navigazione UI solida a 1/3 del costo di Opus |
| Analisi foto | claude-haiku-4-5 | Vision economico, task semplice |
| Valutazioni prodotti | claude-haiku-4-5 | Testo, veloce ed economico |
| Ricerca Vinted | — (API JSON) | Zero token LLM |

---

## Aggiungere un nuovo sito (per sviluppatori)

1. Crea `Ia_personal_shopper/browser/<sito>.py` con la funzione `build_task(query, budget, profilo) -> str`
2. Aggiungilo al dizionario `_TASK_BUILDERS` in `ricerca/coordinatore.py`
3. Aggiungilo a `SITI_SUPPORTATI` e `DOMINI_SITI` in `config.py`

Il task prompt è la logica di navigazione — niente selettori CSS da mantenere.
