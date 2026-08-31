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
  "lunghezza": "corta|regular|lunga"|null, "termini_stile": ["...", "..."]}}

GUSTI DELL'UTENTE (per termini_stile):
stili: {stili}
gli piacciono: {piacciono}
da evitare: {evitare}

Regole:
- query: parole chiave essenziali e ottimizzate per il motore di ricerca Vinted, in italiano.
  TOGLI dalla query: colori (vanno in "colori"), genere, budget e prezzi, parole vuote,
  e le indicazioni di vestibilità o lunghezza (vanno nei campi dedicati: sono filtri sulle
  misure reali del capo, e come parole di ricerca ridurrebbero i risultati).
  MANTIENI i dettagli distintivi del capo (es. "scollo a v", "bootcut", nome modello, brand).
- tipo_capo: "top" (magliette, camicie, felpe, maglioni), "capospalla" (giacche, cappotti,
  bomber, piumini, parka, blazer: tutto ciò che si porta sopra gli altri capi),
  "pantaloni" (anche jeans e shorts), "scarpe", "altro" (accessori, borse, ecc.).
- colori: solo valori tra: {colori}. Se il colore richiesto non è in lista, lascialo nella query.
- genere: solo se esplicito nella richiesta, altrimenti null.
- vestibilita: quanto deve essere ampio il capo. "aderente" (slim, fit, stretto, attillato),
  "oversize" (larga, boxy, comoda, ampia). null se la richiesta NON lo dice.
- lunghezza: quanto deve essere lungo. "corta" (croppata, crop, corta, sopra il fianco),
  "lunga" (lunga, longline, oltre il fianco). null se la richiesta NON lo dice.
  I due campi sono indipendenti: "oversize croppata" → vestibilita oversize, lunghezza corta.
- termini_stile: da 0 a 3 termini di stile presi dai gusti dell'utente qui sopra. Ognuno
  diventa una RICERCA A SÉ, quindi non vanno combinati fra loro: servono tre porte
  d'ingresso diverse allo stesso gusto, non una query più lunga.
  Ogni termine: UNA sola parola (massimo due). Deve restare corto perché la ricerca Vinted
  è un'intersezione, e ogni parola in più fa crollare la quota di capi che dichiarano le
  misure — misurato: "misure spalle lunghezza grunge" tiene il 65%, "misure spalle
  lunghezza band tee" scende al 10%.
  Scegli termini DISCRIMINANTI e diversi tra loro: una sottocultura, una scena o un
  dettaglio costruttivo ("grunge", "western", "goth", "punk", "workwear", "metal",
  "single stitch").
  NON epoche: "vintage", "90s", "y2k", "retro" non discriminano niente, su Vinted li
  scrive metà dei venditori. NON termini generici e onnipresenti come "rock" o "band"
  da soli: appaiono in troppe inserzioni di massa e diluiscono le misure.
  Traduci gli stili dell'utente in come li scrivono i venditori (es. "Modern Western" →
  "western", "Grunge" → "grunge").
  Non usare mai i termini elencati come "da evitare".
  Lista vuota se la richiesta è GIÀ specifica (nomina una band, un brand, un modello o un
  dettaglio preciso), oppure se l'utente non ha stili registrati.

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


# Qualsiasi accenno a come deve vestire il capo. Più larga di _SOLO_VESTIBILITA perché qui
# serve solo a RILEVARE: include i termini ambigui che là non si possono cancellare.
# Volutamente senza "corta"/"lunga": in "manica corta" parlano della manica, non del fit.
_ACCENNO_VESTIBILITA = re.compile(
    r"\b(?:aderent\w*|slim|attillat\w*|skinny|strett\w*|oversize[dn]?|larg[oahi]\w*|boxy"
    r"|comod\w*|ampi[oae]|vestibilit\w*|fit)\b",
    re.IGNORECASE,
)


# "manica corta" / "maniche lunghe" parlano della manica, non della lunghezza del capo. Il
# modello le confonde: su "t-shirt a maniche corte" restituiva lunghezza "corta", e il target
# scendeva a 64cm invece di 72 — 8cm di errore su una richiesta che non chiedeva un crop.
# Concordanze italiane per esteso: "lungo/lunga" non hanno l'h, "lunghi/lunghe" sì. Scrivere
# lungh[aeio] le mancava tutte e quattro — e la stessa svista in entrambe le regex faceva
# passare il test su "manica lunga" per caso invece che per funzionamento.
_FORME_LUNGHEZZA = r"cort[oaie]|lung[oa]|lungh[ie]"
_MANICHE = re.compile(rf"manich?[ae]\s+(?:{_FORME_LUNGHEZZA})", re.IGNORECASE)
_ACCENNO_LUNGHEZZA = re.compile(
    rf"\b(?:cropp\w*|crop|cropped|longline|{_FORME_LUNGHEZZA})\b",
    re.IGNORECASE,
)


def _lunghezza_richiesta(testo: str) -> bool:
    """True se la richiesta dice qualcosa sulla lunghezza del CAPO, non della manica."""
    return bool(_ACCENNO_LUNGHEZZA.search(_MANICHE.sub(" ", testo)))


def _vestibilita_richiesta(testo: str) -> bool:
    """True se la richiesta dice qualcosa su come deve vestire il capo.

    Il prompt chiede null quando la richiesta non lo dice, ma il modello risponde spesso
    "regular" per riflesso. Senza questo controllo il default del profilo non scatta mai, e
    per un utente che preferisce aderente il target sbaglia di 2cm sulle spalle e 3 sul
    petto — cioè esattamente quello che questa funzione dovrebbe garantire.
    """
    return bool(_ACCENNO_VESTIBILITA.search(testo))


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


# Termini di gusto troppo comuni nei titoli: portano gusto ma azzerano le misure
# (misurato: "misure spalle lunghezza rock" → 15% di capi con misure, contro il 65% di
# "misure spalle lunghezza grunge"). Come termine unico non vanno usati.
_GUSTO_TROPPO_COMUNE = {"rock", "band", "tee", "graphic", "streetwear", "denim"}


def _termine_stile_valido(termine: str, stili: set[str]) -> bool:
    """True se il termine è di stile vero e abbastanza selettivo da non diluire le misure.

    Senza questo controllo la scelta del modello oscilla tra esecuzioni identiche: una
    volta "grunge", quella dopo "vintage" — che su Vinted non seleziona niente e riporta il
    mainstream. Ogni termine si prende una fetta del budget di ricerca, quindi un termine
    debole non è neutro: sottrae candidati a quelli buoni.
    """
    token = {t for t in re.findall(r"[\w'-]+", termine.lower())}
    if len(token) > 2 or not token:
        return False
    if token & (EPOCHE_GENERICHE | _GUSTO_TROPPO_COMUNE):
        return False
    return bool(token & (stili | _MARCATORI_STILE))


MAX_TERMINI_STILE = 3


def filtra_termini_stile(termini: list[str], stili: set[str]) -> list[str]:
    """I termini validi, deduplicati e troncati a MAX_TERMINI_STILE.

    La deduplica è sui token e non sulla stringa: "band tee" e "tee" pescherebbero quasi
    lo stesso insieme, e sprecherebbero due delle tre fette di ricerca su un gusto solo.
    """
    validi: list[str] = []
    visti: set[str] = set()
    for termine in termini:
        pulito = " ".join((termine or "").lower().split())
        token = frozenset(re.findall(r"[\w'-]+", pulito))
        if not _termine_stile_valido(pulito, stili) or token & visti:
            continue
        visti |= token
        validi.append(pulito)
        if len(validi) == MAX_TERMINI_STILE:
            break
    return validi


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
    # Il classificatore deterministico ha l'ultima parola quando riconosce il capo: senza,
    # la ricerca usava il tipo dedotto dall'LLM mentre feed e /parere usavano questo, e la
    # stessa "giacca di jeans" veniva giudicata come top in un caso e capospalla nell'altro.
    tipo_certo = tipo_capo_da_titolo(f"{testo} {params.query}")
    if tipo_certo != "altro":
        params.tipo_capo = tipo_certo
    if params.genere not in ("uomo", "donna"):
        params.genere = None
    params.genere = params.genere or rileva_genere(testo, default=profilo.genere)
    params.colori = [c for c in params.colori if c.lower() in COLOR_IDS]
    params.query = _togli_colori(params.query, params.colori)
    # Se la richiesta non dice come deve vestire, decide il profilo; solo in ultima istanza
    # "regular". Così "cerca una t-shirt nera" rispetta la vestibilità preferita dell'utente
    # invece di ignorarla.
    if params.vestibilita not in _VESTIBILITA or not _vestibilita_richiesta(testo):
        params.vestibilita = profilo.vestibilita_preferita
    if params.vestibilita not in _VESTIBILITA:
        params.vestibilita = "regular"
    if params.lunghezza not in _LUNGHEZZE or not _lunghezza_richiesta(testo):
        params.lunghezza = "regular"
    # Un termine di stile debole spreca la fetta di ricerca che gli è riservata: meglio
    # nessuno, e il coordinatore usa tutto il budget per la caccia pura alle misure.
    stili_utente, _ = vocabolari_gusto(profilo)
    params.termini_stile = filtra_termini_stile(params.termini_stile, stili_utente)
    return params


def color_ids(colori: list[str]) -> str | None:
    """Mappa i nomi colore sull'ID Vinted, formato CSV per il parametro color_ids."""
    ids = [str(COLOR_IDS[c.lower()]) for c in colori if c.lower() in COLOR_IDS]
    return ",".join(ids) or None


# Classificazione del capo dal solo titolo, per i capi che non nascono da una richiesta
# (feed Vinted): serve a scegliere quali misure target confrontare. Deterministica e
# gratuita — l'alternativa sarebbe una chiamata LLM per ogni capo del feed.
# I capispalla si portano sopra altri capi, quindi hanno target propri (vedi valutazione/fit):
# più margine su spalle e petto, e nessun controllo sulla lunghezza — un bomber da 68cm e un
# trench da 95cm sono entrambi corretti, e un target unico li boccerebbe entrambi.
_PAROLE_CAPOSPALLA = (
    "giacc", "cappott", "giubbott", "bomber", "piumino", "parka", "trench", "blazer",
    "montgomery", "impermeabil", "windbreaker", "k-way", "softshell", "gilet", "smanicato",
)

_TIPO_DA_PAROLA: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("scarpe", ("scarp", "sneaker", "stival", "scarpon", "mocassin", "sandal", "anfibi",
                "boots", "ciabatt", "infradito")),
    ("pantaloni", ("pantalon", "jeans", "short", "bermuda", "chino", "cargo", "legging",
                   "denim")),
    ("top", ("t-shirt", "tshirt", "t shirt", "maglietta", "maglion", "maglia", "felpa",
             "camicia", "camicetta", "polo", "cardigan", "canotta", "hoodie", "sweater",
             "sweatshirt", "pullover", "tee")),
)


def tipo_capo_da_titolo(titolo: str) -> str:
    """"capospalla" | "top" | "pantaloni" | "scarpe" | "altro" dal titolo di un'inserzione.

    L'ordine dei controlli conta. I capispalla vengono per primi perché il tessuto non fa il
    capo: "giacca di jeans" è un capospalla, non un pantalone. La camicia resta un top anche
    se di denim. Scarpe e pantaloni precedono i top per lo stesso motivo.
    """
    t = titolo.lower()
    if any(p in t for p in _PAROLE_CAPOSPALLA):
        return "capospalla"
    if "camicia" in t or "camicetta" in t:
        return "top"
    for tipo, parole in _TIPO_DA_PAROLA:
        if any(p in t for p in parole):
            return tipo
    return "altro"


def taglie_per_tipo(tipo_capo: str, profilo: ProfiloUtente) -> list[str] | None:
    """La sola taglia del profilo pertinente al tipo di capo ("altro" → nessun filtro taglia)."""
    taglia = {
        "top": profilo.taglie.top,
        "capospalla": profilo.taglie.top,     # le giacche seguono la taglia dei top
        "pantaloni": profilo.taglie.pantaloni,
        "scarpe": profilo.taglie.scarpe,
    }.get(tipo_capo)
    return [taglia] if taglia else None


if __name__ == "__main__":
    # Self-check offline (le parti pure); la parte LLM richiede ANTHROPIC_API_KEY e rete.
    from Ia_personal_shopper.models import TaglieUtente

    assert color_ids(["bianco", "blu marino"]) == "12,27"
    assert color_ids(["fucsia acceso"]) is None

    # Il presidio sul termine di stile: deve essere di stile, corto e selettivo
    stili = {"grunge", "western", "rock", "indie"}
    assert _termine_stile_valido("grunge", stili)
    assert _termine_stile_valido("western", stili)
    assert _termine_stile_valido("single stitch", set())       # marcatore, non nel profilo
    # Un'epoca non discrimina niente su Vinted
    assert not _termine_stile_valido("vintage", stili)
    assert not _termine_stile_valido("y2k", stili)
    # Nel profilo ma troppo comune nei titoli: porta gusto e azzera le misure
    assert not _termine_stile_valido("rock", stili)
    assert not _termine_stile_valido("band tee", stili)
    # Né uno stile, né una frase intera, né vuoto
    assert not _termine_stile_valido("cotone", stili)
    assert not _termine_stile_valido("t-shirt grunge single stitch", stili)
    assert not _termine_stile_valido("", stili)

    # Più termini: ognuno è una ricerca a sé, quindi i doppioni di gusto vanno tolti
    assert filtra_termini_stile(["Grunge", "vintage", "western"], stili) == ["grunge", "western"]
    assert filtra_termini_stile(["band tee", "tee", "goth"], stili) == ["goth"]
    # "single stitch" e "stitch" pescherebbero lo stesso insieme: una fetta sprecata
    assert filtra_termini_stile(["single stitch", "stitch"], set()) == ["single stitch"]
    assert filtra_termini_stile(["grunge", "western", "goth", "punk"], stili | {"goth", "punk"}) == [
        "grunge", "western", "goth",
    ], "mai più di MAX_TERMINI_STILE fette"
    assert filtra_termini_stile([], stili) == []

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

    # Rilevamento della vestibilità nella richiesta: decide se vale il default del profilo
    assert _vestibilita_richiesta("voglio una t-shirt aderente")
    assert _vestibilita_richiesta("felpa oversize")
    assert _vestibilita_richiesta("jeans un po' larghi")
    assert _vestibilita_richiesta("maglietta comoda")
    # "manica corta" parla della manica, non di come veste: deve valere il profilo
    assert not _vestibilita_richiesta("maglietta t-shirt a maniche corte")
    assert not _vestibilita_richiesta("t-shirt nera con stampa")
    assert not _vestibilita_richiesta("camicia a manica lunga")

    # La lunghezza della manica non è la lunghezza del capo: 8cm di target in ballo
    assert not _lunghezza_richiesta("maglietta t-shirt a maniche corte")
    assert not _lunghezza_richiesta("camicia a manica lunga")
    assert not _lunghezza_richiesta("t-shirt nera con stampa")
    assert _lunghezza_richiesta("maglietta a manica corta un po' croppata")
    assert _lunghezza_richiesta("t-shirt corta")
    assert _lunghezza_richiesta("felpa longline")
    assert _lunghezza_richiesta("maglione lungo")
    assert _lunghezza_richiesta("maglioni lunghi")
    assert not _lunghezza_richiesta("camicie a maniche lunghe")
    assert not _lunghezza_richiesta("polo manica corta")
    # Il ripiego che rende inutile fidarsi del prompt: la manica non muove il target
    assert _MANICHE.sub("", "t-shirt a maniche corte").strip() == "t-shirt a"

    # Classificazione dal titolo, per i capi del feed che non nascono da una richiesta
    assert tipo_capo_da_titolo("T-shirt vintage Nike taglia L") == "top"
    assert tipo_capo_da_titolo("Maglietta Hard Rock") == "top"
    assert tipo_capo_da_titolo("Jeans Levi's 501 W32") == "pantaloni"
    assert tipo_capo_da_titolo("Pantaloncini Bermuda Bershka") == "pantaloni"
    assert tipo_capo_da_titolo("Sneakers Nike Air Max 47") == "scarpe"
    assert tipo_capo_da_titolo("Barbie vintage silkstone") == "altro"
    # Il tessuto non fa il capo: una giacca di jeans è un capospalla, non un pantalone
    assert tipo_capo_da_titolo("Giacca di jeans Levi's") == "capospalla"
    assert tipo_capo_da_titolo("Cappotto Timberland") == "capospalla"
    assert tipo_capo_da_titolo("Bomber vintage nero") == "capospalla"
    assert tipo_capo_da_titolo("Piumino North Face") == "capospalla"
    # ...ma la camicia resta un top anche se di denim
    assert tipo_capo_da_titolo("Camicia Lee Western denim nera") == "top"

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

        # Il tipo deve coincidere con quello che userebbero feed e /parere sullo stesso capo
        giacca = asyncio.run(interpreta_ricerca("giacca di jeans, max 40€", p))
        print(giacca)
        assert giacca.tipo_capo == "capospalla", giacca
        pant = asyncio.run(interpreta_ricerca("jeans neri", p))
        assert pant.tipo_capo == "pantaloni", pant
    print("OK")
