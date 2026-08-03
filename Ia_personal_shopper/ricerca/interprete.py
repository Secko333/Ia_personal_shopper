"""Interprete della richiesta: da testo libero a parametri strutturati per la ricerca.

Una chiamata Haiku trasforma es. "maglietta bianca scollo a v" in
{query: "maglietta scollo a v", tipo_capo: "top", colori: ["bianco"]}: il colore
diventa un filtro API reale di Vinted (color_ids) e il filtro taglia usa SOLO la
taglia del profilo pertinente al tipo di capo (top→L, pantaloni→32, scarpe→47).
Se la chiamata LLM fallisce si ricade sul parsing regex (ricerca comunque funzionante).
"""

from __future__ import annotations

import json
import re

import anthropic

from Ia_personal_shopper.config import MODELLO_VALUTAZIONE
from Ia_personal_shopper.models import ParametriRicerca, ProfiloUtente
from Ia_personal_shopper.profilo.gusti import EPOCHE_GENERICHE, vocabolari_gusto
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
  "genere": "uomo"|"donna"|null, "vestibilita": "aderente|regular|oversize"|null,
  "lunghezza": "corta|regular|lunga"|null, "varianti_gusto": ["...", "..."]}}

GUSTI DELL'UTENTE (per varianti_gusto):
stili: {stili}
gli piacciono: {piacciono}
da evitare: {evitare}

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
  "oversize" (larga, boxy, comoda, ampia). null se la richiesta NON lo dice.
- lunghezza: quanto deve essere lungo. "corta" (croppata, crop, corta, sopra il fianco),
  "lunga" (lunga, longline, oltre il fianco). null se la richiesta NON lo dice.
  I due campi sono indipendenti: "oversize croppata" → vestibilita oversize, lunghezza corta.
- varianti_gusto: 0-2 query alternative nello stesso formato di "query", che spingono la
  ricerca verso i gusti dell'utente elencati sopra. Sono l'UNICA ricerca che verrà
  eseguita, quindi devono essere buone: su una query neutra Vinted restituisce il
  mainstream (Hugo Boss, Disney, Ralph Lauren) e non capi di suo gusto.
  REGOLA VINCOLANTE: ogni variante deve contenere almeno un termine di stile
  DISCRIMINANTE, cioè una sottocultura, una scena musicale o un dettaglio costruttivo
  ("grunge", "band tee", "single stitch", "western", "goth", "punk", "workwear", "metal").
  NON basta un'epoca: "vintage", "90s", "y2k" e "retro" da soli non discriminano niente,
  su Vinted li scrive metà dei venditori. Una variante come "t-shirt vintage maniche corte"
  è SBAGLIATA perché non porta nessuno stile: preferisci "t-shirt grunge single stitch".
  Traduci gli stili dell'utente in termini che i venditori scrivono davvero nei titoli
  (es. "Modern Western" → "western", "Alt-Rock" → "band tee rock").
  Tieni le varianti corte: 3-5 parole, senza le parole già ovvie del tipo di capo.
  Non usare mai i termini elencati come "da evitare".
  Restituisci [] se la richiesta è GIÀ specifica (nomina una band, un brand, un modello o
  un dettaglio preciso): in quel caso le varianti aggiungerebbero solo rumore.

Richiesta: {testo}"""

_VESTIBILITA = ("aderente", "regular", "oversize")
_LUNGHEZZE = ("corta", "regular", "lunga")

# Sottoculture e dettagli costruttivi che i venditori Vinted scrivono davvero nei titoli:
# rendono una variante discriminante anche se il termine non è nel profilo dell'utente.
_MARCATORI_STILE = {
    "band", "tee", "grunge", "punk", "metal", "goth", "gothic", "western", "cowboy",
    "workwear", "militare", "military", "skate", "surf", "streetwear", "hardcore",
    "stitch", "bootleg", "tour", "concert", "rock", "indie", "emo", "hippie", "boho",
    "preppy", "techwear", "distressed", "acid", "psichedelico", "graphic", "denim",
}


# Termini di sola vestibilità: vanno nei campi dedicati, e come parole di ricerca riducono
# i risultati (pochi venditori italiani scrivono "croppata"). Il prompt chiede di toglierli
# ma non è affidabile, quindi si tolgono qui. Volutamente NON contiene "corta", "lunga",
# "larga": lì il termine distingue il capo — togliere "lunga" da "manica lunga" lo rovina.
_SOLO_VESTIBILITA = re.compile(
    r"\b(?:cropp\w*|crop|cropped|oversize[dn]?|boxy|aderent\w*|attillat\w*|slim|longline)\b",
    re.IGNORECASE,
)


def _togli_vestibilita(query: str) -> str:
    """Rimuove dalla query i termini di sola vestibilità, lasciandola non vuota."""
    pulita = re.sub(r"\s+", " ", _SOLO_VESTIBILITA.sub(" ", query)).strip()
    return pulita or query


def forme_colore(colore: str) -> list[str]:
    """Varianti di genere e numero di un colore italiano: "bianco" → bianca, bianchi, bianche."""
    c = colore.lower().strip()
    if len(c) <= 3:                      # "blu", "oro": non concordano
        return [c]
    if c.endswith(("o", "a")):
        radice = c[:-1]
        return [radice + s for s in ("o", "a", "i", "e", "he", "hi")]
    if c.endswith("e"):
        return [c, c[:-1] + "i"]
    return [c]


def _togli_colori(query: str, colori: list[str]) -> str:
    """Toglie dalla query i colori già estratti: là sono un filtro API Vinted, qui rumore.

    Il prompt lo chiede ma il modello a volte lascia il colore in entrambi i posti, e la
    parola in più restringe i risultati senza aggiungere selettività.
    """
    if not colori:
        return query
    forme = sorted({f for c in colori for f in forme_colore(c)}, key=len, reverse=True)
    alternative = "|".join(re.escape(f) for f in forme)
    pulita = re.sub(rf"\b(?:{alternative})\b", " ", query, flags=re.IGNORECASE)
    pulita = re.sub(r"\s+", " ", pulita).strip()
    return pulita or query


def _variante_discriminante(variante: str, stili: set[str]) -> bool:
    """True se la variante porta almeno un termine di stile vero.

    Senza questo controllo la qualità delle varianti oscilla tra esecuzioni: una volta
    "t-shirt grunge single stitch", quella dopo "t-shirt vintage maniche corte" — che
    riporta il mainstream, perché "vintage" su Vinted non seleziona niente. Le varianti
    sono l'unica ricerca eseguita, quindi una variante debole costa l'intera lista.
    """
    token = {
        t for t in re.findall(r"[\w'-]+", variante.lower())
        if t not in EPOCHE_GENERICHE
    }
    return bool(token & (stili | _MARCATORI_STILE))


async def interpreta_ricerca(testo: str, profilo: ProfiloUtente) -> ParametriRicerca:
    """Interpreta la richiesta con Haiku; su qualsiasi errore ricade sul parsing regex."""
    try:
        client = anthropic.AsyncAnthropic()
        resp = await client.messages.create(
            model=MODELLO_VALUTAZIONE,
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": _PROMPT.format(
                    colori=", ".join(COLOR_IDS),
                    stili=", ".join(profilo.preferenze_stile) or "non specificato",
                    piacciono=", ".join(profilo.gusti_positivi) or "niente registrato",
                    evitare=", ".join(profilo.gusti_negativi) or "niente registrato",
                    testo=testo,
                ),
            }],
        )
        raw = resp.content[0].text
        dati = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
        params = ParametriRicerca.model_validate(dati)
        if not params.query.strip():
            raise ValueError("query vuota dall'interprete")
    except Exception:
        params = ParametriRicerca(query=pulisci_query(testo))

    params.query = _togli_vestibilita(params.query)
    if params.genere not in ("uomo", "donna"):
        params.genere = None
    params.genere = params.genere or rileva_genere(testo, default=profilo.genere)
    params.colori = [c for c in params.colori if c.lower() in COLOR_IDS]
    params.query = _togli_colori(params.query, params.colori)
    # Se la richiesta non dice come deve vestire, decide il profilo; solo in ultima istanza
    # "regular". Così "cerca una t-shirt nera" rispetta la vestibilità preferita dell'utente
    # invece di ignorarla.
    if params.vestibilita not in _VESTIBILITA:
        params.vestibilita = profilo.vestibilita_preferita
    if params.vestibilita not in _VESTIBILITA:
        params.vestibilita = "regular"
    if params.lunghezza not in _LUNGHEZZE:
        params.lunghezza = "regular"
    # Varianti vuote, duplicate o senza un vero termine di stile non aggiungono candidati:
    # le prime due solo attesa, la terza mainstream. Se cadono tutte, il coordinatore
    # ricade sulla query neutra.
    stili_utente, _ = vocabolari_gusto(profilo)
    viste = {params.query.strip().lower()}
    varianti = []
    for v in params.varianti_gusto:
        v = (v or "").strip()
        if not v or v.lower() in viste:
            continue
        if not _variante_discriminante(v, stili_utente):
            continue
        viste.add(v.lower())
        varianti.append(v)
    params.varianti_gusto = varianti[:2]
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

    # Il presidio sulle varianti: un'epoca da sola non è uno stile
    stili = {"grunge", "western", "rock", "indie"}
    assert _variante_discriminante("t-shirt grunge single stitch", stili)
    assert _variante_discriminante("camicia western denim", stili)
    assert _variante_discriminante("t-shirt band tee", set())        # marcatore, non nel profilo
    assert not _variante_discriminante("t-shirt vintage maniche corte", stili)
    assert not _variante_discriminante("maglietta anni 90 y2k retro", stili)
    assert not _variante_discriminante("t-shirt uomo cotone", stili)

    # I termini di sola vestibilità escono dalla query; quelli che descrivono il capo restano
    assert _togli_vestibilita("maglietta manica corta croppata") == "maglietta manica corta"
    assert _togli_vestibilita("felpa oversize nera") == "felpa nera"
    assert _togli_vestibilita("jeans slim aderenti") == "jeans"
    assert _togli_vestibilita("maglietta manica lunga") == "maglietta manica lunga"
    assert _togli_vestibilita("t-shirt corta") == "t-shirt corta"
    assert _togli_vestibilita("croppata") == "croppata"     # mai svuotare la query

    # Il colore esce dalla query quando è già diventato filtro API, in ogni concordanza
    assert _togli_colori("maglietta bianca scollo a v", ["bianco"]) == "maglietta scollo a v"
    assert _togli_colori("scarpe bianche", ["bianco"]) == "scarpe"
    assert _togli_colori("felpa nera", ["nero"]) == "felpa"
    assert _togli_colori("jeans blu", ["blu"]) == "jeans"
    assert _togli_colori("camicia verde", ["verde"]) == "camicia"
    # Colori diversi con radice simile non si mangiano a vicenda
    assert _togli_colori("maglia rossa", ["rosa"]) == "maglia rossa"
    assert _togli_colori("maglia rosa", ["rosa"]) == "maglia"
    # Senza colori estratti la query non si tocca
    assert _togli_colori("maglietta bianca", []) == "maglietta bianca"
    assert _togli_colori("bianco", ["bianco"]) == "bianco"   # mai svuotare la query

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
