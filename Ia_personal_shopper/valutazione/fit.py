"""Misure target del capo dal profilo fisico, e confronto con le misure reali del venditore.

Una taglia non dice niente: due t-shirt "L" su Vinted vanno da 66 a 78cm di lunghezza.
Qui la richiesta ("una maglietta un po' croppata") diventa misure target concrete derivate
dal profilo fisico, le misure dichiarate dal venditore vengono estratte (dalla descrizione
e, in fallback, dalle foto), e il confronto produce il punteggio con cui ordinare e scartare.

Il punteggio è calcolato in Python, non dall'LLM: ricerche identiche danno lo stesso
ranking. All'LLM resta la sola estrazione, che è quello che sa fare bene.
"""

from __future__ import annotations

import json
import re

import anthropic

from Ia_personal_shopper.config import (
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
from Ia_personal_shopper.ricerca.interprete import taglie_per_tipo
from Ia_personal_shopper.vinted_api import taglia_compatibile, taglia_piu_completa

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

# Oltre questo scarto il capo viene scartato. Le due soglie servono a tenere insieme due
# richieste: le misure devono essere precise, ma lunghezza e spalle contano più del petto.
# Il petto ha quindi una banda più larga — non l'immunità: prima non poteva scartare affatto,
# e un capo col petto sbagliato di 9cm veniva presentato come "su misura".
# Se le liste tornano troppo corte, è SCARTO_MAX_CM il numero da alzare (la CLI stampa il
# miglior capo scartato proprio per far vedere quando conviene).
SCARTO_MAX_CM = 4.0
SCARTO_MAX_SECONDARIO_CM = 8.0

# Deroga per (tipo_capo, campo) dove la banda standard è troppo severa. La lunghezza totale
# di un pantalone contiene il cavallo, che varia di 5-6cm fra un vita bassa e un vita alta:
# con i 4cm si scartavano jeans della lunghezza giusta per come sono tagliati in vita.
SCARTO_MAX_DEROGA = {("pantaloni", "lunghezza_cm"): 7.0}

# Le tre classi di certezza, nell'ordine in cui compaiono in lista.
CLASSE_SU_MISURA = 0      # tutte le misure prioritarie dichiarate e dentro tolleranza
CLASSE_PARZIALE = 1       # quel che dichiara è giusto, ma manca una misura prioritaria
CLASSE_NON_DICHIARATE = 2  # il venditore non ha scritto nessuna misura

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
    # ponytail: banda unica per top e pantaloni, quindi larga fino a 125 — un pantalone da
    # 118cm è normale. Il prezzo è che un 118 letto per sbaglio su una t-shirt non viene più
    # buttato qui, ma scartato dopo per lo scarto sul target. Se capita davvero, la banda va
    # resa dipendente dal tipo di capo (normalizza riceverebbe tipo_capo).
    "lunghezza_cm": (40.0, 125.0),
    "vita_flat_cm": (25.0, 75.0),
    "lunghezza_interna_cm": (55.0, 105.0),
}

# Peso 3 = misura prioritaria, se sfora scarta il capo. Peso 1 = influenza solo il
# punteggio. Per "scarpe" e "altro" non si valutano misure: il filtro taglia già basta.
PESO_PRIORITARIO = 3
PESI: dict[str, dict[str, int]] = {
    "top": {"lunghezza_cm": 3, "spalle_cm": 3, "petto_flat_cm": 1},
    # Capispalla: nessuna lunghezza. Un bomber da 68cm e un trench da 95cm sono entrambi
    # corretti, quindi un target unico li boccerebbe entrambi. Decidono le spalle.
    "capospalla": {"spalle_cm": 3, "petto_flat_cm": 1},
    # Pantaloni: lunghezza totale (vita bassa 104-105cm vs vita alta 110-111cm), interno gamba
    # opzionale. La L dichiarata nel titolo è confrontata a parte (taglia_compatibile).
    "pantaloni": {"vita_flat_cm": 3, "lunghezza_cm": 3, "lunghezza_interna_cm": 1},
}

# Margine in più per i capispalla: si portano sopra un maglione, quindi le misure di un
# cappotto giudicate con le tolleranze di una t-shirt lo fanno sembrare giusto quando è
# stretto sopra gli strati.
EASE_CAPOSPALLA_SPALLE = 2
EASE_CAPOSPALLA_PETTO = 5

# Pantaloni: cavallo medio (distanza vita-cavallo) per ricavare la lunghezza totale da quella
# interna. La manopola per tarare se i capi risultano troppo corti o lunghi: più comune
# 24-26 per vita media, ±1 per vita bassa/alta.
CAVALLO_CM = 26

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

    if tipo_capo in ("top", "capospalla"):
        ease_spalle = EASE_CAPOSPALLA_SPALLE if tipo_capo == "capospalla" else 0
        ease_petto = EASE_CAPOSPALLA_PETTO if tipo_capo == "capospalla" else 0
        if f.larghezza_spalle_cm:
            t.spalle_cm = f.larghezza_spalle_cm + _asse(SPALLE_DELTA, vestibilita) + ease_spalle
        if f.circonferenza_petto_cm:
            t.petto_flat_cm = (
                f.circonferenza_petto_cm / 2 + _asse(PETTO_EASE, vestibilita) + ease_petto
            )
        # La lunghezza si valuta solo sui top: per i capispalla dipende dal modello.
        if tipo_capo == "top" and f.altezza_cm:
            base = round(f.altezza_cm * COEFF_LUNGHEZZA_TOP)
            t.lunghezza_cm = base + _asse(LUNGH_OFFSET, lunghezza)

    elif tipo_capo == "pantaloni":
        if f.circonferenza_vita_cm:
            t.vita_flat_cm = f.circonferenza_vita_cm / 2 + _asse(VITA_EASE, vestibilita)
        if f.lunghezza_gamba_interna_cm:
            t.lunghezza_interna_cm = float(f.lunghezza_gamba_interna_cm)
            # Lunghezza totale: interno gamba + cavallo (distanza vita-cavallo della persona)
            t.lunghezza_cm = f.lunghezza_gamba_interna_cm + CAVALLO_CM

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
    """Confronta le misure del capo con le target e lo colloca in una delle tre classi.

    Ogni misura dichiarata che sfora la propria tolleranza scarta il capo: se il venditore
    l'ha scritta e non va bene, non c'è niente da interpretare. Un capo senza misure non
    viene mai scartato — non sappiamo se va bene — ma finisce in fondo alla lista.
    """
    pesi = PESI.get(tipo_capo, {})
    peso_totale = sum(p for c, p in pesi.items() if getattr(target, c) is not None)

    somma = 0.0
    peso_coperto = 0
    dettagli: list[str] = []
    mancanti: list[str] = []
    prioritarie_mancanti = 0
    peggiore: tuple[float, str] | None = None   # (|scarto|, motivo) della misura fuori tolleranza

    for campo, peso in pesi.items():
        tgt = getattr(target, campo)
        if tgt is None:
            continue                      # misura non nel profilo: fuori dal conteggio
        val = getattr(misure, campo, None) if misure else None
        if val is None:
            mancanti.append(ETICHETTE[campo])
            if peso >= PESO_PRIORITARIO:
                prioritarie_mancanti += 1
            continue

        scarto = val - tgt
        somma += _punteggio_misura(scarto) * peso
        peso_coperto += peso
        dettagli.append(_dettaglio(campo, val, scarto))

        limite = SCARTO_MAX_DEROGA.get(
            (tipo_capo, campo),
            SCARTO_MAX_CM if peso >= PESO_PRIORITARIO else SCARTO_MAX_SECONDARIO_CM,
        )
        if abs(scarto) > limite:
            motivo = f"{ETICHETTE[campo]} {_num(val)} vs {_num(round(tgt))} ({scarto:+.0f})"
            if peggiore is None or abs(scarto) > peggiore[0]:
                peggiore = (abs(scarto), motivo)

    if not peso_coperto:
        return EsitoFit(
            classe=CLASSE_NON_DICHIARATE, punteggio=0.0, confidenza=0.0,
            dettaglio="misure non dichiarate",
        )

    if mancanti:
        dettagli.append(f"{', '.join(mancanti)} n/d")

    # "Su misura" richiede che tutte le prioritarie siano dichiarate: un capo di cui
    # conosciamo una misura su tre non è certo, per quanto quella sia giusta.
    classe = CLASSE_SU_MISURA if prioritarie_mancanti == 0 else CLASSE_PARZIALE

    return EsitoFit(
        classe=classe,
        punteggio=somma / peso_coperto,
        confidenza=peso_coperto / peso_totale if peso_totale else 0.0,
        scartato=peggiore is not None,
        motivo_scarto=peggiore[1] if peggiore else None,
        scarto_max_cm=peggiore[0] if peggiore else 0.0,
        dettaglio=" · ".join(dettagli),
    )


def fascia(esito: EsitoFit | None) -> int:
    """La classe di certezza del capo, 0 = su misura. È il criterio primario di ordinamento."""
    return esito.classe if esito is not None else CLASSE_NON_DICHIARATE


# Indicizzate per classe: etichetta e ordinamento non possono divergere.
_ETICHETTE_FASCIA = [
    ("🎯 SU MISURA", "bold cyan"),
    ("◐ MISURE PARZIALI", "cyan"),
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

def _numero_nel_testo(numero: float, testo: str) -> bool:
    """Il numero compare davvero nel testo di origine?

    L'estrattore inventa: su un cappotto che dichiarava solo spalle e lunghezza ha restituito
    un petto di 37cm che nella descrizione non esiste. Con la regola per cui ogni misura
    dichiarata fuori tolleranza esclude il capo, un numero inventato ne uccide uno buono in
    silenzio. Il controllo si applica al valore grezzo, prima della normalizzazione, perché
    una circonferenza di 102 dimezzata a 51 non comparirebbe come "51" nel testo.
    """
    forme = {f"{numero:g}", f"{numero:.0f}", f"{numero:g}".replace(".", ",")}
    return any(
        re.search(rf"(?<!\d){re.escape(f)}(?!\d)", testo)
        for f in forme
    )


def _misure_da_dict(dati: dict, fonte: str, testo: str | None = None) -> MisureCapo:
    """Misure dal JSON dell'estrattore. Con `testo`, scarta i numeri che non vi compaiono."""
    valori: dict[str, float] = {}
    for campo in _CAMPI:
        v = dati.get(campo)
        if v is None:
            continue
        try:
            numero = float(v)
        except (TypeError, ValueError):
            continue
        if testo is not None and not _numero_nel_testo(numero, testo):
            continue                      # misura inventata: si butta
        valori[campo] = numero
    return normalizza(MisureCapo(fonte=fonte, **valori))


# Colore e vestibilità escono dallo stesso modello che legge le misure, e come i numeri
# vanno validati: chiesto il colore, il modello lo deduce volentieri dal brand o dal modello
# invece di leggerlo, e in tabella compare un "nero" che il venditore non ha mai scritto.
# Il confronto è per radice (parola meno l'ultima lettera) così le concordanze italiane
# passano comunque: "nero" vale su un titolo che dice "nera", "ampio" su "ampia".
def _radice(parola: str) -> str:
    return parola[:-1] if len(parola) > 3 else parola


def _parole_nel_testo(valore, testo: str, max_parole: int = 3) -> str | None:
    """Il valore normalizzato se ogni sua parola compare nel testo, altrimenti None."""
    pulito = " ".join(str(valore or "").lower().split())
    parole = pulito.split()
    if not parole or len(parole) > max_parole:
        return None
    basso = testo.lower()
    return pulito if all(_radice(p) in basso for p in parole) else None


def _ha_misure(misure: MisureCapo) -> bool:
    return any(getattr(misure, c) is not None for c in _CAMPI)


def unisci_misure(base: MisureCapo | None, nuove: MisureCapo) -> MisureCapo:
    """Completa le misure esistenti con quelle lette dalle foto, senza sovrascriverle.

    Una misura scritta dal venditore è più affidabile di un numero letto in una fotografia:
    se c'è già, resta quella. Le foto servono a colmare i buchi, non a rimpiazzare.
    """
    if base is None:
        return nuove
    unite = base.model_copy()
    for campo in _CAMPI:
        if getattr(unite, campo) is None and getattr(nuove, campo) is not None:
            setattr(unite, campo, getattr(nuove, campo))
    if base.fonte and nuove.fonte and base.fonte != nuove.fonte:
        unite.fonte = f"{base.fonte}+{nuove.fonte}"
    return unite


_PROMPT_DESCRIZIONI = """Estrai le misure del capo dalle descrizioni e titoli di venditori Vinted.

Per ogni capo restituisci un oggetto con il suo indice e SOLO i dati che il venditore
dichiara esplicitamente. Non stimare, non dedurre: se un dato non c'è, ometti il campo.
Ometti l'intero oggetto solo se il capo non dichiara né misure, né colore, né vestibilità.

Sinonimi da riconoscere:
- spalle_cm: "spalle", "larghezza spalle", "da spalla a spalla", "shoulder"
- petto_flat_cm: "petto", "torace", "ascella-ascella", "da ascella a ascella", "pit to pit", "p2p", "larghezza torace"
- lunghezza_cm: "lunghezza", "lungh", "lunghezza totale", "lunghezza sul retro", "altezza"
- vita_flat_cm: "vita", "girovita", "cintura", "waist"
- lunghezza_interna_cm: "interno gamba", "cavallo", "inseam", "lunghezza interna"
- taglia_disponibile: taglia dichiarata nel titolo o nella descrizione (es. "M", "L", "W32 L36", "IT 42")
- colore: il colore del capo, se il venditore lo SCRIVE (1-2 parole: "nero", "verde militare").
  Non ricavarlo dalla foto e non dedurlo dal brand o dal modello: se non è scritto, ometti.
- vestibilita: come il venditore dice che veste ("oversize", "slim", "vestibilità ampia",
  "regular", "boxy"). Solo se lo scrive; una taglia non è una vestibilità.

Riporta i numeri esattamente come li scrive il venditore, senza convertirli né dimezzarli.

Rispondi SOLO con JSON:
{{"misure": [{{"indice": 1, "spalle_cm": 53, "petto_flat_cm": 57, "lunghezza_cm": 78,
  "taglia_disponibile": "L", "colore": "nero", "vestibilita": "oversize"}}]}}

TITOLI E DESCRIZIONI:
{descrizioni}"""


async def estrai_da_descrizioni(prodotti: list[ProdottoRisultato]) -> dict[int, MisureCapo | tuple[MisureCapo, str]] | None:
    """indice 1..N → misure, in una sola chiamata Haiku.

    Ritorna None se la chiamata è fallita, {} se nessun venditore dichiara misure: la
    differenza va mostrata all'utente, altrimenti una chiave API rotta è indistinguibile
    da "nessuno ha scritto le misure" e la ricerca degrada in silenzio.

    Se la taglia è dichiarata nel titolo/descrizione, ritorna (misure, taglia_str).

    Effetto collaterale voluto: scrive anche `colore` e `fit_dichiarato` sui prodotti. Sono
    nella stessa chiamata perché il testo da leggere è lo stesso — una seconda chiamata per
    due stringhe sarebbe il doppio del costo per zero informazione in più.
    """
    blocchi = [
        f"[{i}] TITOLO: {p.nome}\nDESCRIZIONE: {p.descrizione[:600] if p.descrizione else '(nessuna)'}"
        for i, p in enumerate(prodotti, start=1)
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

    esito: dict[int, MisureCapo | tuple[MisureCapo, str]] = {}
    for voce in dati.get("misure") or []:
        try:
            indice = int(voce.get("indice"))
        except (TypeError, ValueError):
            continue
        if not 1 <= indice <= len(prodotti):
            continue
        # Validato contro titolo + descrizione di QUEL capo: un numero preso da un altro capo
        # della lista, o inventato, viene scartato.
        prodotto = prodotti[indice - 1]
        testo_validazione = (prodotto.nome or "") + "\n" + (prodotto.descrizione or "")
        # Colore e vestibilità si scrivono sul prodotto e non nell'esito: valgono anche per i
        # capi senza misure, che finiscono in coda ma vanno comunque descritti all'utente.
        prodotto.colore = _parole_nel_testo(voce.get("colore"), testo_validazione, max_parole=2)
        prodotto.fit_dichiarato = _parole_nel_testo(voce.get("vestibilita"), testo_validazione)
        misure = _misure_da_dict(
            voce, fonte="descrizione", testo=testo_validazione
        )
        if _ha_misure(misure):
            taglia_letta = voce.get("taglia_disponibile")
            if taglia_letta:
                esito[indice] = (misure, str(taglia_letta))
            else:
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


async def misure_da_immagini(immagini: list[tuple[str, str]]) -> MisureCapo | None:
    """Misure leggibili in un elenco di immagini già (media_type, base64).

    Unico ingresso rimasto per la vision, e serve solo alle foto che l'utente allega a
    /parere: là non c'è né titolo né descrizione da leggere. Le inserzioni non passano più
    da qui — le misure si prendono da titolo e descrizione, che il venditore scrive.
    """
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

    L'affinità sta dentro la classe e non sopra: un capo del gusto giusto che non veste
    resta inutile. Ma dentro la stessa classe il gusto viene prima della precisione fine,
    perché la differenza tra 1cm e 3cm di scarto non si vede addosso mentre quella tra una
    band tee e una polo Ralph Lauren sì.

    Lo spareggio finale è la rilevanza, non il prezzo: ordinare per prezzo crescente
    riempiva la lista di magliette da 2€ invece dei capi pertinenti.
    """
    return (
        fascia(pa.fit),
        -pa.affinita_gusto,
        -(pa.fit.punteggio if pa.fit else 0.0),
        pa.prodotto.rilevanza,
    )


def ordina_per_fit(arricchiti: list[ProdottoArricchito]) -> list[ProdottoArricchito]:
    """Ordina per fascia di vestibilità, poi gusto, poi rilevanza.

    Pubblica perché serve anche a chi valuta più gruppi separati (il feed Vinted mescola
    top, pantaloni e scarpe): concatenare i gruppi già ordinati non basta, l'ordine finale
    va rifatto sull'insieme.
    """
    return sorted(arricchiti, key=_chiave_ordine)


async def seleziona(
    prodotti: list[ProdottoRisultato],
    profilo: ProfiloUtente,
    params: ParametriRicerca,
) -> tuple[list[ProdottoArricchito], list[ProdottoArricchito], ReportFit]:
    """Estrae misure da titolo + descrizione, scarta i fuori misura, ordina per fit.

    Ritorna (mostrati, coda, report): i capi "su misura" o "parziali" nei mostrati,
    i "non dichiarate" in coda (ordinati per gusto). Se il tipo di capo non prevede
    misure, il fit resta disattivato e si usa il gusto.
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
        return arricchiti[:MAX_RISULTATI_FINALI], [], report

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

    taglie_utente = taglie_per_tipo(params.tipo_capo, profilo)

    mostrati: list[ProdottoArricchito] = []
    coda: list[ProdottoArricchito] = []
    for i, p in enumerate(prodotti, start=1):
        dato = misure_per_indice.get(i)
        misure, taglia_letta = dato if isinstance(dato, tuple) else (dato, None)

        # La taglia scritta nel titolo può essere più completa di quella dell'API: sui jeans
        # Vinted dà "W32 | IT 46" e la lunghezza sta solo nel titolo.
        p.taglia_disponibile = taglia_piu_completa(p.taglia_disponibile, taglia_letta)
        if taglie_utente and not taglia_compatibile(p.taglia_disponibile, taglie_utente):
            report.fuori_taglia += 1
            continue

        esito = valuta(misure, target, params.tipo_capo)
        if esito.scartato:
            registra_scarto(esito)
            continue

        pa = con_gusto(p, misure=misure, fit=esito)
        # Il taglio è "il venditore ha scritto delle misure o no": quelle parziali restano
        # in lista col loro dettaglio, quelle assenti vanno in coda e le si guarda a richiesta.
        (coda if esito.classe == CLASSE_NON_DICHIARATE else mostrati).append(pa)

    mostrati.sort(key=_chiave_ordine)
    coda.sort(key=_chiave_ordine)
    coda = coda[:MAX_RISULTATI_FINALI]
    report.in_coda = len(coda)

    return mostrati[:MAX_RISULTATI_FINALI], coda, report


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

    # Pantaloni: si giudicano su vita e lunghezza TOTALE, quella che i venditori scrivono
    # davvero. L'interno gamba resta come controllo, ma non è più obbligatorio: pretenderlo
    # mandava ogni jeans in "misure parziali", perché quasi nessuno lo dichiara.
    t_pant = misure_target(riccardo, "pantaloni", "regular", "regular")
    assert t_pant.vita_flat_cm == 42 and t_pant.lunghezza_interna_cm == 88
    assert t_pant.lunghezza_cm == 114, t_pant.lunghezza_cm       # 88 interno + 26 di cavallo

    # Jeans reale che dichiara solo vita e lunghezza: è "su misura", non "parziale"
    jeans = MisureCapo(vita_flat_cm=42, lunghezza_cm=112)
    e_jeans = valuta(jeans, t_pant, "pantaloni")
    assert e_jeans.classe == CLASSE_SU_MISURA, e_jeans
    assert not e_jeans.scartato, e_jeans        # −2cm di lunghezza: dentro la banda dei 7cm
    # Il cavallo varia di 5-6cm fra vita bassa e vita alta: la banda larga serve a non
    # scartare jeans giusti, ma non è immunità.
    assert not valuta(MisureCapo(vita_flat_cm=42, lunghezza_cm=108), t_pant, "pantaloni").scartato
    corti = valuta(MisureCapo(vita_flat_cm=42, lunghezza_cm=100), t_pant, "pantaloni")
    assert corti.scartato, "14cm di lunghezza in meno devono scartare il capo"
    # La vita resta severa: 4cm come le altre misure prioritarie
    assert valuta(MisureCapo(vita_flat_cm=48, lunghezza_cm=114), t_pant, "pantaloni").scartato
    # L'interno gamba dichiarato si controlla, ma la sua assenza non declassa il capo
    assert "interno gamba n/d" in e_jeans.dettaglio, e_jeans.dettaglio
    assert valuta(
        MisureCapo(vita_flat_cm=42, lunghezza_cm=114, lunghezza_interna_cm=70),
        t_pant, "pantaloni",
    ).scartato, "un interno gamba di 18cm in meno resta un capo sbagliato"

    # Capispalla: margine per gli strati sotto, e nessun target di lunghezza
    t_capo = misure_target(riccardo, "capospalla", "regular", "regular")
    assert t_capo.spalle_cm == 54, t_capo.spalle_cm      # 58 − 6 + 2
    assert t_capo.petto_flat_cm == 56, t_capo.petto_flat_cm  # 102/2 + 0 + 5
    assert t_capo.lunghezza_cm is None, "un capospalla non ha una lunghezza giusta unica"

    # Il cappotto Timberland dell'esempio reale: spalle 51, lunghezza 74 (non valutata)
    timberland = MisureCapo(spalle_cm=51, lunghezza_cm=74)
    e_timb = valuta(timberland, t_capo, "capospalla")
    assert not e_timb.scartato, e_timb                    # 51 vs 54 = −3, dentro i 4cm
    assert e_timb.classe == CLASSE_SU_MISURA
    assert "lungh" not in e_timb.dettaglio, "la lunghezza non deve comparire nel giudizio"
    # Con le tolleranze da t-shirt le stesse spalle sarebbero risultate perfette: il margine
    # dei capispalla è ciò che distingue "giusto" da "stretto sopra un maglione".
    assert valuta(MisureCapo(spalle_cm=48), t_capo, "capospalla").scartato

    # Misura di profilo assente → target None, esclusa dal conteggio
    vuoto = misure_target(ProfiloUtente(), "top")
    assert vuoto.spalle_cm is None and descrivi_target(vuoto) == ""
    assert descrivi_target(t) == "spalle ~52cm · petto ~51cm · lungh ~64cm"

    # Il presidio contro le misure inventate: caso reale, un cappotto che dichiarava solo
    # spalle 51 e lunghezza 74 si portava dietro un petto di 37cm mai scritto da nessuno.
    testo_reale = "Larghezza spalle circa 51 cm\nLunghezza totale 74 cm"
    pulite = _misure_da_dict(
        {"spalle_cm": 51, "lunghezza_cm": 74, "petto_flat_cm": 37}, "descrizione", testo_reale
    )
    assert pulite.spalle_cm == 51 and pulite.lunghezza_cm == 74
    assert pulite.petto_flat_cm is None, "un numero assente dal testo non è una misura"
    # Senza testo di riferimento (lettura dalle foto) il controllo non si applica
    assert _misure_da_dict({"petto_flat_cm": 37}, "foto").petto_flat_cm == 37
    # Il controllo guarda il valore grezzo: 102 dimezzato a 51 non comparirebbe come "51"
    assert _misure_da_dict({"petto_flat_cm": 102}, "descrizione", "Petto 102cm").petto_flat_cm == 51
    # Confini di cifra: "51" non deve essere trovato dentro "151"
    assert not _numero_nel_testo(51, "Lunghezza 151 cm")
    assert _numero_nel_testo(51, "spalle 51cm")
    assert _numero_nel_testo(74, "Lunghezza totale 74 cm,")

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

    # Caso limite tenuto: spalle −3 e petto +4 stanno dentro le rispettive tolleranze
    dickies = MisureCapo(spalle_cm=49, petto_flat_cm=55, lunghezza_cm=65)
    e_dick = valuta(dickies, t, "top")
    assert not e_dick.scartato, e_dick
    assert e_dick.classe == CLASSE_SU_MISURA and e_dick.confidenza == 1.0
    assert "spalle 49 (-3, stretto)" in e_dick.dettaglio, e_dick.dettaglio
    assert "lungh 65 ✓" in e_dick.dettaglio

    # Il petto ha una banda più larga (8cm) ma NON è immune: prima non scartava mai, e un
    # capo col petto sbagliato di 9cm veniva presentato come "su misura".
    quasi = MisureCapo(spalle_cm=52, lunghezza_cm=64, petto_flat_cm=56)   # petto +5
    assert not valuta(quasi, t, "top").scartato
    troppo_largo = MisureCapo(spalle_cm=52, lunghezza_cm=64, petto_flat_cm=60)  # petto +9
    e_largo = valuta(troppo_largo, t, "top")
    assert e_largo.scartato, "un petto fuori di 9cm deve scartare il capo"
    assert "petto 60 vs 51 (+9)" == e_largo.motivo_scarto, e_largo.motivo_scarto

    # Capo senza misure: mai scartato, ma ultimo in lista ed etichettato
    e_nulla = valuta(None, t, "top")
    assert not e_nulla.scartato and e_nulla.confidenza == 0.0
    assert e_nulla.classe == CLASSE_NON_DICHIARATE
    assert e_nulla.dettaglio == "misure non dichiarate"
    assert etichetta_fit(e_nulla)[0] == "📏 misure non dichiarate"

    # Misure parziali: quel che dichiara è giusto, ma manca una prioritaria → non è certo
    e_parz = valuta(MisureCapo(lunghezza_cm=64), t, "top")
    assert e_parz.classe == CLASSE_PARZIALE, "una misura su tre non è 'su misura'"
    assert not e_parz.scartato and e_parz.confidenza == 3 / 7
    assert "spalle, petto n/d" in e_parz.dettaglio, e_parz.dettaglio
    assert etichetta_fit(e_parz)[0] == "◐ MISURE PARZIALI"

    # Il petto è opzionale: lunghezza e spalle giuste bastano per essere certi
    e_senza_petto = valuta(MisureCapo(spalle_cm=52, lunghezza_cm=64), t, "top")
    assert e_senza_petto.classe == CLASSE_SU_MISURA, e_senza_petto
    # ...ma se il petto c'è e sfora, il capo esce comunque
    assert valuta(MisureCapo(spalle_cm=52, lunghezza_cm=64, petto_flat_cm=62), t, "top").scartato

    # Unione: le foto colmano i buchi, non sovrascrivono la descrizione del venditore
    da_descrizione = MisureCapo(spalle_cm=52, fonte="descrizione")
    da_foto = MisureCapo(spalle_cm=45, lunghezza_cm=64, fonte="foto")
    unite = unisci_misure(da_descrizione, da_foto)
    assert unite.spalle_cm == 52, "la misura scritta dal venditore deve vincere"
    assert unite.lunghezza_cm == 64 and unite.fonte == "descrizione+foto"
    assert unisci_misure(None, da_foto) is da_foto

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

    # Classi ed etichette non possono divergere: sono indicizzate dalla stessa funzione
    assert fascia(None) == CLASSE_NON_DICHIARATE
    assert fascia(valuta(perfetto, t, "top")) == CLASSE_SU_MISURA
    assert len(_ETICHETTE_FASCIA) == 3
    assert all(etichetta_fit(EsitoFit(classe=c, punteggio=1, confidenza=1))[0]
               for c in (CLASSE_SU_MISURA, CLASSE_PARZIALE, CLASSE_NON_DICHIARATE))

    # La classe resta sopra il gusto: un capo del gusto giusto che non veste non serve
    fuori_gusto = _pa("Polo Ralph Lauren", 30, perfetto, rilevanza=0)
    di_gusto = _pa("Band tee vintage", 30, MisureCapo(lunghezza_cm=64), rilevanza=9)
    di_gusto.affinita_gusto = 3
    assert fascia(fuori_gusto.fit) == CLASSE_SU_MISURA
    assert fascia(di_gusto.fit) == CLASSE_PARZIALE
    assert [pa.prodotto.nome for pa in sorted([di_gusto, fuori_gusto], key=_chiave_ordine)] == [
        "Polo Ralph Lauren", "Band tee vintage"
    ]

    # Stessa classe, gusto diverso → vince il gusto anche se la rilevanza è peggiore
    a_pari = _pa("Hugo Boss", 30, MisureCapo(lunghezza_cm=64), rilevanza=0)
    assert fascia(a_pari.fit) == fascia(di_gusto.fit)
    assert [pa.prodotto.nome for pa in sorted([a_pari, di_gusto], key=_chiave_ordine)] == [
        "Band tee vintage", "Hugo Boss"
    ]

    # Stessa classe e stesso gusto → vince chi è più preciso sui centimetri
    preciso = _pa("preciso", 30, MisureCapo(spalle_cm=52, lunghezza_cm=64), rilevanza=5)
    approssimato = _pa("approssimato", 30, dickies, rilevanza=0)
    assert fascia(preciso.fit) == fascia(approssimato.fit) == CLASSE_SU_MISURA
    assert [pa.prodotto.nome for pa in sorted([approssimato, preciso], key=_chiave_ordine)] == [
        "preciso", "approssimato"
    ]

    # Il taglio fra mostrati e coda: le misure parziali restano in lista col loro dettaglio,
    # solo l'assenza totale di misure manda in coda. Senza misure il capo non si scarta —
    # non sappiamo se va bene — ma non si mostra nemmeno senza che l'utente lo chieda.
    assert valuta(MisureCapo(lunghezza_cm=64), t, "top").classe != CLASSE_NON_DICHIARATE
    assert valuta(None, t, "top").classe == CLASSE_NON_DICHIARATE
    assert not valuta(None, t, "top").scartato

    # La taglia letta nel titolo e il confronto W/L, che è ciò che tiene fuori i jeans corti
    from Ia_personal_shopper.vinted_api import taglia_compatibile, taglia_piu_completa

    assert taglia_piu_completa("W32 | IT 46", "W32 L34") == "W32 L34"
    assert taglia_compatibile(taglia_piu_completa("W32 | IT 46", "W32 L34"), ["w32 l36"])
    assert not taglia_compatibile(taglia_piu_completa("W32 | IT 46", "W32 L30"), ["w32 l36"])
    # Senza la fusione la L non entrerebbe nel confronto e il capo sbagliato passerebbe
    assert taglia_compatibile("W32 | IT 46", ["w32 l36"])

    # Colore e vestibilità: si accettano solo se il venditore li ha scritti davvero
    annuncio = "Felpa Carhartt verde militare\nVestibilità oversize, taglia L, come nuova"
    assert _parole_nel_testo("verde militare", annuncio, max_parole=2) == "verde militare"
    assert _parole_nel_testo("Oversize", annuncio) == "oversize"
    # Le concordanze italiane passano: il modello scrive "nero", il venditore ha scritto "nera"
    assert _parole_nel_testo("nero", "T-shirt nera Nirvana") == "nero"
    assert _parole_nel_testo("ampio", "vestibilità ampia") == "ampio"
    # Dedotto dalla foto o dal brand invece che letto: non deve arrivare in tabella
    assert _parole_nel_testo("blu", annuncio) is None
    assert _parole_nel_testo("slim", annuncio) is None
    assert _parole_nel_testo(None, annuncio) is None
    assert _parole_nel_testo("", annuncio) is None
    # Una frase non è un attributo
    assert _parole_nel_testo("verde militare con stampa", annuncio, max_parole=2) is None

    print("OK")
