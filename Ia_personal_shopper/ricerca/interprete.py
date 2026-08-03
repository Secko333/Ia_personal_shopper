"""Interprete della richiesta: da testo libero a parametri strutturati per la ricerca.

Una chiamata Haiku trasforma es. "maglietta bianca scollo a v" in
{query: "maglietta scollo a v", tipo_capo: "top", colori: ["bianco"]}: il colore
diventa un filtro API reale di Vinted (color_ids) e il filtro taglia usa SOLO la
taglia del profilo pertinente al tipo di capo (top→L, pantaloni→32, scarpe→47).
Se la chiamata LLM fallisce si ricade sul parsing regex (ricerca comunque funzionante).
"""

from __future__ import annotations

import json

import anthropic

from Ia_personal_shopper.config import MODELLO_VALUTAZIONE
from Ia_personal_shopper.models import ParametriRicerca, ProfiloUtente
from Ia_personal_shopper.ricerca.aggregatore import pulisci_query, rileva_genere

# ID colore Vinted, verificati live da GET /api/v2/colors (luglio 2026).
COLOR_IDS = {
    "nero": 1, "grigio": 3, "bianco": 12, "panna": 20, "beige": 4,
    "albicocca": 21, "arancione": 11, "corallo": 22, "rosso": 7, "borgogna": 23,
    "rosa": 5, "viola": 6, "lilla": 25, "azzurro": 26, "blu": 9,
    "blu marino": 27, "turchese": 17, "menta": 30, "verde": 10,
    "verde scuro": 28, "cachi": 16, "marrone": 2, "senape": 29, "giallo": 8,
    "argento": 13, "oro": 14, "multi": 15, "chiaro": 32,
}

_PROMPT = """Sei l'interprete delle ricerche di un personal shopper che cerca capi su Vinted.
Trasforma la richiesta dell'utente in parametri di ricerca. Rispondi SOLO con JSON:
{{"query": "...", "tipo_capo": "top|pantaloni|scarpe|altro", "colori": [...],
  "genere": "uomo"|"donna"|null, "vestibilita": "aderente|regular|oversize",
  "lunghezza": "corta|regular|lunga"}}

Regole:
- query: parole chiave essenziali e ottimizzate per il motore di ricerca Vinted, in italiano.
  TOGLI dalla query: colori (vanno in "colori"), genere, budget e prezzi, parole vuote,
  e le indicazioni di vestibilità o lunghezza (vanno nei campi dedicati: sono filtri sulle
  misure reali del capo, e come parole di ricerca ridurrebbero i risultati).
  MANTIENI i dettagli distintivi del capo (es. "scollo a v", "bootcut", nome modello, brand).
- tipo_capo: "top" (magliette, camicie, felpe, maglioni, giacche), "pantaloni" (anche jeans e shorts),
  "scarpe", "altro" (accessori, borse, ecc.).
- colori: solo valori tra: {colori}. Se il colore richiesto non è in lista, lascialo nella query.
- genere: solo se esplicito nella richiesta, altrimenti null.
- vestibilita: quanto deve essere ampio il capo. "aderente" (slim, fit, stretto, attillato),
  "oversize" (larga, boxy, comoda, ampia), "regular" se l'utente non lo dice.
- lunghezza: quanto deve essere lungo. "corta" (croppata, crop, corta, sopra il fianco),
  "lunga" (lunga, longline, oltre il fianco), "regular" se l'utente non lo dice.
  I due campi sono indipendenti: "oversize croppata" → vestibilita oversize, lunghezza corta.

Richiesta: {testo}"""

_VESTIBILITA = ("aderente", "regular", "oversize")
_LUNGHEZZE = ("corta", "regular", "lunga")


async def interpreta_ricerca(testo: str, profilo: ProfiloUtente) -> ParametriRicerca:
    """Interpreta la richiesta con Haiku; su qualsiasi errore ricade sul parsing regex."""
    try:
        client = anthropic.AsyncAnthropic()
        resp = await client.messages.create(
            model=MODELLO_VALUTAZIONE,
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": _PROMPT.format(colori=", ".join(COLOR_IDS), testo=testo),
            }],
        )
        raw = resp.content[0].text
        dati = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
        params = ParametriRicerca.model_validate(dati)
        if not params.query.strip():
            raise ValueError("query vuota dall'interprete")
    except Exception:
        params = ParametriRicerca(query=pulisci_query(testo))

    if params.genere not in ("uomo", "donna"):
        params.genere = None
    params.genere = params.genere or rileva_genere(testo, default=profilo.genere)
    params.colori = [c for c in params.colori if c.lower() in COLOR_IDS]
    # Valori fuori vocabolario ricadono su "regular": le misure target restano sensate
    # anche per una richiesta che non dice niente sulla vestibilità.
    if params.vestibilita not in _VESTIBILITA:
        params.vestibilita = "regular"
    if params.lunghezza not in _LUNGHEZZE:
        params.lunghezza = "regular"
    return params


def color_ids(colori: list[str]) -> str | None:
    """Mappa i nomi colore sull'ID Vinted, formato CSV per il parametro color_ids."""
    ids = [str(COLOR_IDS[c.lower()]) for c in colori if c.lower() in COLOR_IDS]
    return ",".join(ids) or None


def taglie_per_tipo(tipo_capo: str, profilo: ProfiloUtente) -> list[str] | None:
    """La sola taglia del profilo pertinente al tipo di capo ("altro" → nessun filtro taglia)."""
    taglia = {
        "top": profilo.taglie.top,
        "pantaloni": profilo.taglie.pantaloni,
        "scarpe": profilo.taglie.scarpe,
    }.get(tipo_capo)
    return [taglia] if taglia else None


if __name__ == "__main__":
    # Self-check offline (le parti pure); la parte LLM richiede ANTHROPIC_API_KEY e rete.
    from Ia_personal_shopper.models import TaglieUtente

    assert color_ids(["bianco", "blu marino"]) == "12,27"
    assert color_ids(["fucsia acceso"]) is None

    p = ProfiloUtente(taglie=TaglieUtente(top="L", pantaloni="32", scarpe="47"))
    assert taglie_per_tipo("top", p) == ["L"]
    assert taglie_per_tipo("pantaloni", p) == ["32"]
    assert taglie_per_tipo("altro", p) is None

    import asyncio
    import os

    if os.environ.get("ANTHROPIC_API_KEY"):
        params = asyncio.run(interpreta_ricerca("maglietta bianca scollo a v non sopra i 20 euro", p))
        print(params)
        assert "bianc" not in params.query.lower()
        assert params.tipo_capo == "top"
        assert params.colori == ["bianco"]
        assert (params.vestibilita, params.lunghezza) == ("regular", "regular")

        crop = asyncio.run(interpreta_ricerca("maglietta a manica corta un po' croppata", p))
        print(crop)
        assert crop.lunghezza == "corta", crop
        assert "cropp" not in crop.query.lower(), crop.query   # va nel campo, non nella query

        over = asyncio.run(interpreta_ricerca("felpa oversize croppata nera", p))
        print(over)
        assert (over.vestibilita, over.lunghezza) == ("oversize", "corta"), over
    print("OK")
