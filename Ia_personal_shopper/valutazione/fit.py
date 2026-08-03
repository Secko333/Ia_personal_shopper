"""Misure target del capo dal profilo fisico, e confronto con le misure reali del venditore.

Una taglia non dice niente: due t-shirt "L" su Vinted vanno da 66 a 78cm di lunghezza.
Qui la richiesta ("una maglietta un po' croppata") diventa misure target concrete derivate
dal profilo fisico, le misure dichiarate dal venditore vengono estratte (dalla descrizione
e, in fallback, dalle foto), e il confronto produce il punteggio con cui ordinare e scartare.

Il punteggio è calcolato in Python, non dall'LLM: ricerche identiche danno lo stesso
ranking. All'LLM resta la sola estrazione, che è quello che sa fare bene.
"""

from __future__ import annotations

import asyncio
import base64
import json

import anthropic

from Ia_personal_shopper.config import (
    MAX_CAPI_VISION,
    MAX_RISULTATI_FINALI,
    MODELLO_VALUTAZIONE,
    MODELLO_VISION,
)
from Ia_personal_shopper.models import (
    EsitoFit,
    MisureCapo,
    MisureTarget,
    ParametriRicerca,
    ProdottoArricchito,
    ProdottoRisultato,
    ProfiloUtente,
    ReportFit,
)
from Ia_personal_shopper.profilo.gusti import affinita_gusto, vocabolari_gusto

# ---------------------------------------------------------------------------
# Taratura — unico punto da correggere quando i consigli sbagliano di qualche cm
# ---------------------------------------------------------------------------

# Scarto tra misura del corpo e misura del capo, per asse di vestibilità.
SPALLE_DELTA = {"aderente": -8, "regular": -6, "oversize": -2}   # spalle_corpo + delta
PETTO_EASE = {"aderente": -3, "regular": 0, "oversize": 6}       # petto_corpo/2 + ease
VITA_EASE = {"aderente": -1, "regular": 1, "oversize": 4}        # vita_corpo/2 + ease

# La lunghezza dei top è derivata dall'altezza invece di essere una tabella di cm fissi,
# così resta corretta se l'altezza nel profilo cambia. Su 194cm dà 64 / 72 / 77.
COEFF_LUNGHEZZA_TOP = 0.372
LUNGH_OFFSET = {"corta": -8, "regular": 0, "lunga": 5}

# Oltre questo scarto, su una misura prioritaria (peso 3), il capo viene scartato.
# Soglia severa e voluta: se le ricerche tornano quasi vuote, è questo il numero da alzare
# (la CLI stampa il miglior capo scartato proprio per far vedere quando conviene).
SCARTO_MAX_CM = 4.0

# Petto e vita oltre questo valore sono circonferenze, non misure piatte: i venditori
# scrivono sia "Torace: 57" (ascella-ascella) sia "Petto 102cm" (giro). Non si applica alle
# spalle, dove la circonferenza non è una convenzione: 90cm di spalle è solo un errore.
SOGLIA_CIRCONFERENZA_CM = 70.0

# Banda di plausibilità per un capo da adulto: fuori da qui il numero è spazzatura
# (visti sul campo "Spalle 90cm" e "Spalle 18,5cm") e va trattato come non dichiarato,
# altrimenti sporca il punteggio e fa scartare capi buoni.
PLAUSIBILI = {
    "spalle_cm": (30.0, 70.0),
    "petto_flat_cm": (30.0, 80.0),
    "lunghezza_cm": (40.0, 110.0),
    "vita_flat_cm": (25.0, 75.0),
    "lunghezza_interna_cm": (55.0, 105.0),
}

# Peso 3 = misura prioritaria, se sfora scarta il capo. Peso 1 = influenza solo il
# punteggio. Per "scarpe" e "altro" non si valutano misure: il filtro taglia già basta.
PESO_PRIORITARIO = 3
PESI: dict[str, dict[str, int]] = {
    "top": {"lunghezza_cm": 3, "spalle_cm": 3, "petto_flat_cm": 1},
    "pantaloni": {"vita_flat_cm": 3, "lunghezza_interna_cm": 3},
}

# L'ordine di questo dict è l'ordine in cui le misure appaiono a schermo.
ETICHETTE = {
    "spalle_cm": "spalle",
    "petto_flat_cm": "petto",
    "lunghezza_cm": "lungh",
    "vita_flat_cm": "vita",
    "lunghezza_interna_cm": "interno gamba",
}
_CAMPI = tuple(ETICHETTE)
_CAMPI_CIRCONFERENZA = ("petto_flat_cm", "vita_flat_cm")

# (troppo poco, troppo) per il commento sullo scarto
_GIUDIZI = {
    "spalle_cm": ("stretto", "largo"),
    "petto_flat_cm": ("stretto", "largo"),
    "vita_flat_cm": ("stretta", "larga"),
    "lunghezza_cm": ("corto", "lungo"),
    "lunghezza_interna_cm": ("corto", "lungo"),
}


# ---------------------------------------------------------------------------
# Misure target dal profilo
# ---------------------------------------------------------------------------

def _asse(tabella: dict[str, int], valore: str) -> int:
    """L'asse arriva da un LLM: qualsiasi valore inatteso ricade su 'regular'."""
    return tabella.get(valore, tabella.get("regular", 0))


def misure_target(
    profilo: ProfiloUtente,
    tipo_capo: str,
    vestibilita: str = "regular",
    lunghezza: str = "regular",
) -> MisureTarget:
    """Misure che il capo dovrebbe avere. Una misura di profilo assente → target None."""
    f = profilo.fisico
    t = MisureTarget()

    if tipo_capo == "top":
        if f.larghezza_spalle_cm:
            t.spalle_cm = f.larghezza_spalle_cm + _asse(SPALLE_DELTA, vestibilita)
        if f.circonferenza_petto_cm:
            t.petto_flat_cm = f.circonferenza_petto_cm / 2 + _asse(PETTO_EASE, vestibilita)
        if f.altezza_cm:
            base = round(f.altezza_cm * COEFF_LUNGHEZZA_TOP)
            t.lunghezza_cm = base + _asse(LUNGH_OFFSET, lunghezza)

    elif tipo_capo == "pantaloni":
        if f.circonferenza_vita_cm:
            t.vita_flat_cm = f.circonferenza_vita_cm / 2 + _asse(VITA_EASE, vestibilita)
        if f.lunghezza_gamba_interna_cm:
            t.lunghezza_interna_cm = float(f.lunghezza_gamba_interna_cm)

    return t


def _num(v: float) -> str:
    return f"{v:g}"


def descrivi_target(target: MisureTarget) -> str:
    """"spalle ~52cm · lungh ~64cm · petto ~51cm" — stringa vuota se non c'è nessuna target."""
    parti = [
        f"{ETICHETTE[c]} ~{_num(round(v))}cm"
        for c in _CAMPI
        if (v := getattr(target, c)) is not None
    ]
    return " · ".join(parti)


# ---------------------------------------------------------------------------
# Normalizzazione e punteggio
# ---------------------------------------------------------------------------

def normalizza(misure: MisureCapo) -> MisureCapo:
    """Porta le misure in forma confrontabile, scartando i numeri non plausibili.

    1. Petto e vita oltre soglia sono circonferenze: si dimezzano. Senza questo, un capo
       con "Petto 102cm" (flat 51, spesso proprio il target) verrebbe confrontato come 102
       e scartato con uno scarto apparente di +51cm.
    2. Quel che resta fuori dalla banda di plausibilità viene azzerato: vale come misura
       non dichiarata, non come misura sbagliata da confrontare.
    """
    for campo in _CAMPI_CIRCONFERENZA:
        v = getattr(misure, campo)
        if v is not None and v > SOGLIA_CIRCONFERENZA_CM:
            setattr(misure, campo, v / 2)

    for campo, (minimo, massimo) in PLAUSIBILI.items():
        v = getattr(misure, campo)
        if v is not None and not minimo <= v <= massimo:
            setattr(misure, campo, None)
    return misure


def _punteggio_misura(scarto: float) -> float:
    a = abs(scarto)
    if a <= 2:
        return 1.0
    if a <= 4:
        return 0.7
    if a <= 6:
        return 0.4
    return 0.0


def _dettaglio(campo: str, valore: float, scarto: float) -> str:
    eti = ETICHETTE[campo]
    if abs(scarto) <= 2:
        return f"{eti} {_num(valore)} ✓"
    poco, troppo = _GIUDIZI[campo]
    parola = poco if scarto < 0 else troppo
    if abs(scarto) > 6:
        parola = f"molto {parola}"
    return f"{eti} {_num(valore)} ({scarto:+.0f}, {parola})"


def valuta(misure: MisureCapo | None, target: MisureTarget, tipo_capo: str) -> EsitoFit:
    """Confronta le misure del capo con le target. Un capo senza misure non viene mai
    scartato: passa con confidenza 0 ed etichetta esplicita."""
    pesi = PESI.get(tipo_capo, {})
    peso_totale = sum(p for c, p in pesi.items() if getattr(target, c) is not None)

    somma = 0.0
    peso_coperto = 0
    dettagli: list[str] = []
    mancanti: list[str] = []
    peggiore: tuple[float, str] | None = None   # (|scarto|, motivo) sulla prioritaria fuori soglia

    for campo, peso in pesi.items():
        tgt = getattr(target, campo)
        if tgt is None:
            continue                      # misura non nel profilo: fuori dal conteggio
        val = getattr(misure, campo, None) if misure else None
        if val is None:
            mancanti.append(ETICHETTE[campo])
            continue

        scarto = val - tgt
        somma += _punteggio_misura(scarto) * peso
        peso_coperto += peso
        dettagli.append(_dettaglio(campo, val, scarto))

        if peso >= PESO_PRIORITARIO and abs(scarto) > SCARTO_MAX_CM:
            motivo = f"{ETICHETTE[campo]} {_num(val)} vs {_num(round(tgt))} ({scarto:+.0f})"
            if peggiore is None or abs(scarto) > peggiore[0]:
                peggiore = (abs(scarto), motivo)

    if not peso_coperto:
        return EsitoFit(punteggio=0.0, confidenza=0.0, dettaglio="misure non dichiarate")

    if mancanti:
        dettagli.append(f"{', '.join(mancanti)} n/d")

    return EsitoFit(
        punteggio=somma / peso_coperto,
        confidenza=peso_coperto / peso_totale if peso_totale else 0.0,
        scartato=peggiore is not None,
        motivo_scarto=peggiore[1] if peggiore else None,
        scarto_max_cm=peggiore[0] if peggiore else 0.0,
        dettaglio=" · ".join(dettagli),
    )


def fascia(esito: EsitoFit | None) -> int:
    """Fascia grossa di fit, 0 = migliore, 3 = senza misure.

    Si ordina per fascia e non per punteggio esatto perché le differenze fini (0.95 contro
    0.93) sono rumore: nascono da bande di tolleranza a gradini, non da una misura più
    precisa. Dentro la fascia decide il gusto (vedi _chiave_ordine).
    """
    if esito is None or esito.confidenza == 0:
        return 3
    if esito.punteggio >= 0.85:
        return 0
    if esito.punteggio >= 0.6:
        return 1
    return 2


# Indicizzate per fascia: etichetta e stile Rich restano allineati alle soglie di ordinamento.
_ETICHETTE_FASCIA = [
    ("🎯 SU MISURA", "bold cyan"),
    ("● OK", "cyan"),
    ("◌ APPROSSIMATIVO", "yellow"),
    ("📏 misure non dichiarate", "dim"),
]


def etichetta_fit(esito: EsitoFit | None) -> tuple[str, str]:
    """(testo, stile Rich) per la riga fit in tabella."""
    if esito is None:
        return "", "dim"
    return _ETICHETTE_FASCIA[fascia(esito)]


# ---------------------------------------------------------------------------
# Estrazione delle misure — descrizioni (una chiamata) e foto (fallback)
# ---------------------------------------------------------------------------

def _misure_da_dict(dati: dict, fonte: str) -> MisureCapo:
    valori: dict[str, float] = {}
    for campo in _CAMPI:
        v = dati.get(campo)
        if v is None:
            continue
        try:
            valori[campo] = float(v)
        except (TypeError, ValueError):
            continue
    return normalizza(MisureCapo(fonte=fonte, **valori))


def _ha_misure(misure: MisureCapo) -> bool:
    return any(getattr(misure, c) is not None for c in _CAMPI)


_PROMPT_DESCRIZIONI = """Estrai le misure del capo dalle descrizioni di venditori Vinted.

Per ogni capo restituisci un oggetto con il suo indice e SOLO le misure che il venditore
dichiara esplicitamente in cm. Non stimare, non dedurre dalla taglia: se una misura non
c'è, ometti il campo. Se un capo non dichiara nessuna misura, ometti l'intero oggetto.

Sinonimi da riconoscere:
- spalle_cm: "spalle", "larghezza spalle", "da spalla a spalla", "shoulder"
- petto_flat_cm: "petto", "torace", "ascella-ascella", "da ascella a ascella", "pit to pit", "p2p", "larghezza torace"
- lunghezza_cm: "lunghezza", "lungh", "lunghezza totale", "lunghezza sul retro", "altezza"
- vita_flat_cm: "vita", "girovita", "cintura", "waist"
- lunghezza_interna_cm: "interno gamba", "cavallo", "inseam", "lunghezza interna"

Riporta il numero esattamente come lo scrive il venditore, senza convertirlo né dimezzarlo.

Rispondi SOLO con JSON:
{{"misure": [{{"indice": 1, "spalle_cm": 53, "petto_flat_cm": 57, "lunghezza_cm": 78}}]}}

DESCRIZIONI:
{descrizioni}"""


async def estrai_da_descrizioni(prodotti: list[ProdottoRisultato]) -> dict[int, MisureCapo] | None:
    """indice 1..N → misure, in una sola chiamata Haiku.

    Ritorna None se la chiamata è fallita, {} se nessun venditore dichiara misure: la
    differenza va mostrata all'utente, altrimenti una chiave API rotta è indistinguibile
    da "nessuno ha scritto le misure" e la ricerca degrada in silenzio.
    """
    blocchi = [
        f"[{i}] {p.descrizione[:600]}"
        for i, p in enumerate(prodotti, start=1)
        if p.descrizione
    ]
    if not blocchi:
        return {}

    try:
        client = anthropic.AsyncAnthropic()
        resp = await client.messages.create(
            model=MODELLO_VALUTAZIONE,
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": _PROMPT_DESCRIZIONI.format(descrizioni="\n\n".join(blocchi)),
            }],
        )
        raw = resp.content[0].text
        dati = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
    except Exception:
        return None

    esito: dict[int, MisureCapo] = {}
    for voce in dati.get("misure") or []:
        try:
            indice = int(voce.get("indice"))
        except (TypeError, ValueError):
            continue
        if not 1 <= indice <= len(prodotti):
            continue
        misure = _misure_da_dict(voce, fonte="descrizione")
        if _ha_misure(misure):
            esito[indice] = misure
    return esito


_PROMPT_FOTO = """Queste sono le foto di un capo in vendita su Vinted. Alcuni venditori
fotografano un metro a nastro appoggiato sul capo, oppure scrivono le misure sull'immagine.

Riporta una misura SOLO se il numero in cm è leggibile nelle foto. Non stimare a occhio,
non dedurre dalle proporzioni o dalla taglia: se non c'è un numero leggibile, ometti il
campo. Un oggetto vuoto è la risposta giusta quando le foto non mostrano misure.

Campi possibili: spalle_cm, petto_flat_cm, lunghezza_cm, vita_flat_cm, lunghezza_interna_cm.
Riporta il numero come è scritto, senza convertirlo.

Rispondi SOLO con JSON, es. {"spalle_cm": 52, "lunghezza_cm": 66} oppure {}"""

_TIPI_IMMAGINE = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def _scarica_foto(urls: list[str]) -> list[tuple[str, str]]:
    """(media_type, base64) per ogni foto scaricabile, tramite la sessione Vinted scaldata."""
    from Ia_personal_shopper import vinted_api

    immagini = []
    for url in urls:
        dati, tipo = vinted_api.scarica_immagine(url)
        if dati:
            if tipo not in _TIPI_IMMAGINE:
                tipo = "image/webp"
            immagini.append((tipo, base64.standard_b64encode(dati).decode()))
    return immagini


async def _misure_da_foto(prodotto: ProdottoRisultato) -> MisureCapo | None:
    """Misure leggibili nelle foto, o None se non ce ne sono.

    Gli errori di chiamata NON vengono inghiottiti: propagano, così il chiamante li conta
    e li mostra invece di farli passare per "il venditore non ha scritto le misure".
    """
    # ponytail: si leggono le foto [2:5] — le prime due sono di presentazione, il metro
    # arriva dopo. Tetto: 3 immagini per capo. Se le misure sfuggono, allargare la fetta.
    urls = prodotto.foto[2:5] or prodotto.foto[:3]
    if not urls:
        return None

    immagini = await asyncio.to_thread(_scarica_foto, urls)
    if not immagini:
        return None

    contenuto: list[dict] = [
        {"type": "image", "source": {"type": "base64", "media_type": tipo, "data": dati}}
        for tipo, dati in immagini
    ]
    contenuto.append({"type": "text", "text": _PROMPT_FOTO})

    client = anthropic.AsyncAnthropic()
    resp = await client.messages.create(
        model=MODELLO_VISION,
        max_tokens=300,
        messages=[{"role": "user", "content": contenuto}],
    )
    raw = resp.content[0].text
    inizio, fine = raw.find("{"), raw.rfind("}") + 1
    if inizio < 0 or fine <= inizio:
        return None
    try:
        dati_json = json.loads(raw[inizio:fine])
    except ValueError:
        return None

    misure = _misure_da_dict(dati_json, fonte="foto")
    return misure if _ha_misure(misure) else None


# ---------------------------------------------------------------------------
# Selezione: dal pool di candidati ai capi da mostrare
# ---------------------------------------------------------------------------

def _chiave_ordine(pa: ProdottoArricchito):
    """Fascia di fit, poi affinità di gusto, poi rilevanza del sito.

    L'affinità sta dentro la fascia e non sopra: un capo del gusto giusto che non entra
    resta inutile. Ma dentro la stessa fascia il gusto viene prima del fit fine, perché la
    differenza tra 0.95 e 0.93 non si vede addosso mentre quella tra una band tee e una
    polo Ralph Lauren sì.

    Lo spareggio finale è la rilevanza, non il prezzo: ordinare per prezzo crescente
    riempiva la lista di magliette da 2€ invece dei capi pertinenti.
    """
    return (
        fascia(pa.fit),
        -pa.affinita_gusto,
        -(pa.fit.confidenza if pa.fit else 0.0),
        pa.prodotto.rilevanza,
    )


async def seleziona(
    prodotti: list[ProdottoRisultato],
    profilo: ProfiloUtente,
    params: ParametriRicerca,
) -> tuple[list[ProdottoArricchito], ReportFit]:
    """Estrae le misure, scarta i fuori misura, ordina per fit e taglia ai risultati finali.

    Se il tipo di capo non prevede misure, o il profilo non ne ha di utili, il fit resta
    disattivato e l'ordine dei prodotti (per prezzo) è preservato.
    """
    target = misure_target(profilo, params.tipo_capo, params.vestibilita, params.lunghezza)
    report = ReportFit(target=target, candidati=len(prodotti))

    positivi, negativi = vocabolari_gusto(profilo)

    def con_gusto(p: ProdottoRisultato, **kw) -> ProdottoArricchito:
        return ProdottoArricchito(
            prodotto=p, affinita_gusto=affinita_gusto(p, positivi, negativi), **kw
        )

    if params.tipo_capo not in PESI or not descrivi_target(target):
        # Nessuna misura da confrontare: resta il gusto a decidere l'ordine.
        arricchiti = [con_gusto(p) for p in prodotti]
        arricchiti.sort(key=_chiave_ordine)
        return arricchiti[:MAX_RISULTATI_FINALI], report

    report.attivo = True
    misure_per_indice = await estrai_da_descrizioni(prodotti)
    if misure_per_indice is None:
        report.errore_descrizioni = True
        misure_per_indice = {}

    # Il "migliore" tra gli scartati è quello che sfora di meno: è il segnale che dice
    # all'utente quando SCARTO_MAX_CM è troppo severo per questa ricerca.
    minimo_sforo = float("inf")

    def registra_scarto(esito: EsitoFit) -> None:
        nonlocal minimo_sforo
        report.scartati += 1
        if esito.scarto_max_cm < minimo_sforo:
            minimo_sforo = esito.scarto_max_cm
            report.miglior_scartato = esito.motivo_scarto

    arricchiti: list[ProdottoArricchito] = []
    for i, p in enumerate(prodotti, start=1):
        misure = misure_per_indice.get(i)
        esito = valuta(misure, target, params.tipo_capo)
        if esito.scartato:
            registra_scarto(esito)
            continue
        arricchiti.append(con_gusto(p, misure=misure, fit=esito))

    # Vision solo sui capi che entrerebbero in lista e non hanno misure: le foto sono
    # gratis in HTTP ma non in token, quindi il numero è limitato.
    arricchiti.sort(key=_chiave_ordine)
    da_leggere = [
        pa for pa in arricchiti[: MAX_RISULTATI_FINALI + 3]
        if pa.fit is not None and pa.fit.confidenza == 0 and pa.prodotto.foto
    ][:MAX_CAPI_VISION]

    if da_leggere:
        letture = await asyncio.gather(
            *(_misure_da_foto(pa.prodotto) for pa in da_leggere),
            return_exceptions=True,
        )
        for pa, misure in zip(da_leggere, letture):
            if isinstance(misure, BaseException):
                report.errori_foto += 1
                continue
            if misure is None:
                continue
            esito = valuta(misure, target, params.tipo_capo)
            report.letti_da_foto += 1
            pa.misure, pa.fit = misure, esito
            if esito.scartato:
                registra_scarto(esito)   # marcato scartato: filtrato subito sotto

        arricchiti = [pa for pa in arricchiti if not (pa.fit and pa.fit.scartato)]
        arricchiti.sort(key=_chiave_ordine)

    return arricchiti[:MAX_RISULTATI_FINALI], report


if __name__ == "__main__":
    # Self-check offline della logica pura; l'estrazione LLM richiede rete e chiave API.
    from Ia_personal_shopper.models import FisicoUtente

    riccardo = ProfiloUtente(
        fisico=FisicoUtente(
            altezza_cm=194,
            larghezza_spalle_cm=58,
            circonferenza_petto_cm=102,
            circonferenza_vita_cm=82,
            lunghezza_gamba_interna_cm=88,
        )
    )

    # Caso canonico: "maglietta a manica corta un po' croppata"
    t = misure_target(riccardo, "top", "regular", "corta")
    assert t.spalle_cm == 52, t.spalle_cm
    assert t.petto_flat_cm == 51, t.petto_flat_cm
    assert t.lunghezza_cm == 64, t.lunghezza_cm          # round(194*0.372)=72, -8

    # Assi combinati: oversize croppata resta esprimibile
    t_over = misure_target(riccardo, "top", "oversize", "corta")
    assert (t_over.spalle_cm, t_over.petto_flat_cm, t_over.lunghezza_cm) == (56, 57, 64)

    t_pant = misure_target(riccardo, "pantaloni", "regular", "regular")
    assert t_pant.vita_flat_cm == 42 and t_pant.lunghezza_interna_cm == 88

    # Misura di profilo assente → target None, esclusa dal conteggio
    vuoto = misure_target(ProfiloUtente(), "top")
    assert vuoto.spalle_cm is None and descrivi_target(vuoto) == ""
    assert descrivi_target(t) == "spalle ~52cm · petto ~51cm · lungh ~64cm"

    # Normalizzazione: "Petto 102cm" è una circonferenza, "Torace 57" è già flat
    assert normalizza(MisureCapo(petto_flat_cm=102)).petto_flat_cm == 51
    assert normalizza(MisureCapo(petto_flat_cm=57)).petto_flat_cm == 57
    # ...ma per le spalle la circonferenza non esiste: 90 è spazzatura, non 45
    assert normalizza(MisureCapo(spalle_cm=90)).spalle_cm is None
    assert normalizza(MisureCapo(spalle_cm=18.5)).spalle_cm is None
    assert normalizza(MisureCapo(lunghezza_cm=7)).lunghezza_cm is None
    assert normalizza(MisureCapo(spalle_cm=52)).spalle_cm == 52
    # Un capo con la sola misura implausibile vale come "misure non dichiarate"
    assert not _ha_misure(normalizza(MisureCapo(spalle_cm=90)))

    # Capi reali raccolti da Vinted il 2026-08-03, contro il target croppata (52/51/64)
    nike = MisureCapo(spalle_cm=53, petto_flat_cm=57, lunghezza_cm=78)
    armani = MisureCapo(spalle_cm=46, petto_flat_cm=51, lunghezza_cm=68)
    hardrock = normalizza(MisureCapo(spalle_cm=48, petto_flat_cm=102, lunghezza_cm=75))

    e_nike = valuta(nike, t, "top")
    assert e_nike.scartato and "lungh 78 vs 64 (+14)" == e_nike.motivo_scarto, e_nike
    assert valuta(armani, t, "top").scartato          # spalle 46 vs 52 (−6) > 4cm
    assert valuta(hardrock, t, "top").scartato        # lungh 75 vs 64 (+11)

    # Caso limite tenuto: spalle −3 dentro soglia, petto +4 sfora ma è peso 1
    dickies = MisureCapo(spalle_cm=49, petto_flat_cm=55, lunghezza_cm=65)
    e_dick = valuta(dickies, t, "top")
    assert not e_dick.scartato, e_dick
    assert e_dick.confidenza == 1.0
    assert "spalle 49 (-3, stretto)" in e_dick.dettaglio, e_dick.dettaglio
    assert "lungh 65 ✓" in e_dick.dettaglio

    # Peso 1 fuori soglia da solo non scarta mai
    assert not valuta(MisureCapo(petto_flat_cm=60), t, "top").scartato

    # Capo senza misure: mai scartato, confidenza 0, etichetta esplicita
    e_nulla = valuta(None, t, "top")
    assert not e_nulla.scartato and e_nulla.confidenza == 0.0
    assert e_nulla.dettaglio == "misure non dichiarate"
    assert etichetta_fit(e_nulla)[0] == "📏 misure non dichiarate"

    # Misure parziali: confidenza proporzionale al peso coperto (lungh 3 su 3+3+1)
    e_parz = valuta(MisureCapo(lunghezza_cm=64), t, "top")
    assert e_parz.confidenza == 3 / 7 and e_parz.punteggio == 1.0
    assert "spalle, petto n/d" in e_parz.dettaglio, e_parz.dettaglio

    # tipo_capo senza misure → nessun fit, nessuno scarto
    assert valuta(nike, t, "altro").confidenza == 0.0

    # Ordinamento: prima i misurati per fit, poi gli "in forse"
    def _pa(nome, prezzo, misure, rilevanza=0):
        p = ProdottoRisultato(
            nome=nome, url=f"u/{nome}", sito="vinted", prezzo=prezzo, rilevanza=rilevanza
        )
        return ProdottoArricchito(prodotto=p, misure=misure, fit=valuta(misure, t, "top"))

    perfetto = MisureCapo(spalle_cm=52, petto_flat_cm=51, lunghezza_cm=64)
    ordinati = sorted(
        [_pa("senza", 5, None), _pa("approx", 50, dickies), _pa("perfetto", 90, perfetto)],
        key=_chiave_ordine,
    )
    assert [pa.prodotto.nome for pa in ordinati] == ["perfetto", "approx", "senza"]

    # A pari fit vince la rilevanza del sito, non il prezzo più basso: senza questo la
    # lista si riempiva di magliette da 2€ senza misure al posto dei capi pertinenti.
    per_rilevanza = sorted(
        [_pa("junk-2euro", 2, None, rilevanza=40), _pa("pertinente", 25, None, rilevanza=1)],
        key=_chiave_ordine,
    )
    assert [pa.prodotto.nome for pa in per_rilevanza] == ["pertinente", "junk-2euro"]

    # Fasce ed etichette non possono divergere: sono indicizzate dalla stessa funzione
    assert fascia(None) == 3 and etichetta_fit(e_nulla)[0] == "📏 misure non dichiarate"
    assert fascia(valuta(perfetto, t, "top")) == 0
    assert fascia(e_dick) == 1, e_dick.punteggio
    assert len(_ETICHETTE_FASCIA) == 4

    # Dentro la stessa fascia decide il gusto, non il punteggio fine
    fuori_gusto = _pa("Polo Ralph Lauren", 30, perfetto, rilevanza=0)
    di_gusto = _pa("Band tee vintage", 30, dickies, rilevanza=9)
    di_gusto.affinita_gusto = 3
    assert fascia(fuori_gusto.fit) == 0 and fascia(di_gusto.fit) == 1
    # ...ma la fascia resta sopra il gusto: un capo che non veste non serve
    assert [pa.prodotto.nome for pa in sorted([di_gusto, fuori_gusto], key=_chiave_ordine)] == [
        "Polo Ralph Lauren", "Band tee vintage"
    ]

    # Stessa fascia, gusto diverso → vince il gusto anche se la rilevanza è peggiore
    a_pari = _pa("Hugo Boss", 30, dickies, rilevanza=0)
    assert fascia(a_pari.fit) == fascia(di_gusto.fit)
    assert [pa.prodotto.nome for pa in sorted([a_pari, di_gusto], key=_chiave_ordine)] == [
        "Band tee vintage", "Hugo Boss"
    ]

    print("OK")
