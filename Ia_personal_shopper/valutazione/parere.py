"""Parere su un singolo capo: conviene comprarlo o no, e perché.

Diverso dalla valutazione di una lista: qui il capo è uno, quindi ci si può permettere una
risposta argomentata invece di dodici parole. Il confronto sulle misure resta deterministico
(valutazione/fit.py) — al modello tocca il giudizio su prezzo, condizioni, stile e
abbinamenti col guardaroba, più l'elenco di cosa chiedere al venditore.
"""

from __future__ import annotations

import json

import anthropic

from Ia_personal_shopper.config import MODELLO_VALUTAZIONE
from Ia_personal_shopper.models import (
    CapoGuardaroba,
    EsitoFit,
    MisureCapo,
    MisureTarget,
    ParereCapo,
    ProdottoRisultato,
    ProfiloUtente,
)
from Ia_personal_shopper.valutazione.consulente import costruisci_contesto_utente
from Ia_personal_shopper.valutazione.fit import (
    CLASSE_NON_DICHIARATE,
    CLASSE_PARZIALE,
    ETICHETTE,
    PESI,
    SCARTO_MAX_CM,
    SCARTO_MAX_SECONDARIO_CM,
    descrivi_target,
)

_PROMPT_SISTEMA = (
    "Sei un consulente di moda italiano esperto, onesto e diretto. Valuti un singolo capo "
    "per un utente di cui conosci fisico, taglie, stile e guardaroba. "
    "Non sei un venditore: se il capo non vale, lo dici senza giri di parole. "
    "Se vale, lo dici con la stessa chiarezza."
)

_PROMPT = """Dai un parere su questo capo per {nome}.

{contesto}

CAPO IN ESAME:
{capo}

{sezione_misure}
Rispondi SOLO con JSON:
{{"verdetto": "compra"|"considera"|"evita",
  "sintesi": "una frase secca che dice se conviene e perché",
  "a_favore": ["...", "..."],
  "contro": ["...", "..."],
  "da_chiedere": ["..."]}}

Regole:
- verdetto: "compra" se conviene davvero, "considera" se dipende da qualcosa da verificare,
  "evita" se no. Sii selettivo: "compra" non è il default.
- sintesi: massimo 25 parole, il punto centrale del giudizio.
- a_favore / contro: da 1 a 4 voci ciascuno, concrete e riferite a QUESTO capo. Cita i
  centimetri quando li hai, il prezzo, le condizioni, lo stile, gli abbinamenti col
  guardaroba. Niente frasi generiche.
- da_chiedere: cosa domandare al venditore prima di comprare — tipicamente le misure che
  mancano. Lista vuota se non serve chiedere nulla.
- Sul prezzo: è seconda mano, quindi valuta il rapporto con le condizioni dichiarate.
"""

_SEZIONE_MISURE_ASSENTI = """MISURE: il capo non dichiara nessuna misura, e non se ne leggono
dalle foto. Non puoi sapere se veste bene: non dire che è della taglia giusta solo perché
l'etichetta lo suggerisce, e metti le misure che servono in "da_chiedere".
"""


def _sezione_misure(
    fit: EsitoFit | None,
    misure: MisureCapo | None,
    target: MisureTarget,
    tipo_capo: str,
) -> str:
    if fit is None or fit.classe == CLASSE_NON_DICHIARATE or misure is None:
        return _SEZIONE_MISURE_ASSENTI

    mancanti = [
        ETICHETTE[campo]
        for campo in PESI.get(tipo_capo, {})
        if getattr(target, campo) is not None and getattr(misure, campo) is None
    ]
    righe = [
        f"MISURE CERCATE: {descrivi_target(target)}",
        f"MISURE DEL CAPO: {fit.dettaglio}",
        f"Fonte: {misure.fonte or 'non indicata'}.",
        f"Tolleranze accettate dall'utente: {SCARTO_MAX_CM:.0f}cm sulle misure principali, "
        f"{SCARTO_MAX_SECONDARIO_CM:.0f}cm sul petto, che per lui conta meno.",
    ]
    if fit.scartato:
        righe.append(
            f"VERDETTO SULLE MISURE: NON VA BENE — {fit.motivo_scarto}. "
            "Questo da solo basta per sconsigliarlo, per quanto il capo sia bello."
        )
    elif fit.classe == CLASSE_PARZIALE:
        righe.append(
            "VERDETTO SULLE MISURE: quel che dichiara è dentro tolleranza, ma manca "
            f"{', '.join(mancanti)}: non c'è certezza. Mettilo in \"da_chiedere\"."
        )
    else:
        righe.append(
            "VERDETTO SULLE MISURE: VA BENE, le misure che contano sono dentro tolleranza. "
            "NON bocciare il capo per uno scarto già riportato qui: è stato giudicato "
            "accettabile. Il tuo giudizio riguarda prezzo, condizioni, stile e abbinamenti."
        )
        if mancanti:
            righe.append(f"Resta non dichiarata: {', '.join(mancanti)}.")
    return "\n".join(righe) + "\n"


async def parere_su_capo(
    prodotto: ProdottoRisultato,
    profilo: ProfiloUtente,
    tipo_capo: str,
    target: MisureTarget,
    misure: MisureCapo | None,
    fit: EsitoFit | None,
    guardaroba: list[CapoGuardaroba] | None = None,
) -> ParereCapo | None:
    """Il parere argomentato, o None se la chiamata non riesce."""
    dettagli = [f"Nome: {prodotto.nome}"]
    if prodotto.brand:
        dettagli.append(f"Brand: {prodotto.brand}")
    if prodotto.prezzo is not None:
        dettagli.append(f"Prezzo: {prodotto.prezzo:.2f}€")
    if prodotto.taglia_disponibile:
        dettagli.append(f"Taglia dichiarata: {prodotto.taglia_disponibile}")
    if prodotto.condizione:
        dettagli.append(f"Condizioni: {prodotto.condizione}")
    dettagli.append(f"Tipo di capo: {tipo_capo}")
    if prodotto.descrizione:
        dettagli.append(f"Descrizione del venditore:\n{prodotto.descrizione[:900]}")

    prompt = _PROMPT.format(
        nome=profilo.nome,
        contesto=costruisci_contesto_utente(profilo, guardaroba, None),
        capo="\n".join(dettagli),
        sezione_misure=_sezione_misure(fit, misure, target, tipo_capo),
    )

    try:
        client = anthropic.AsyncAnthropic()
        resp = await client.messages.create(
            model=MODELLO_VALUTAZIONE,
            max_tokens=1200,
            system=_PROMPT_SISTEMA,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text
        dati = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
        parere = ParereCapo.model_validate(dati)
    except Exception:
        return None

    if parere.verdetto not in ("compra", "considera", "evita"):
        parere.verdetto = "considera"
    # Un capo fuori misura non può risultare consigliato: il confronto sui centimetri è
    # deterministico e non si lascia ribaltare dal giudizio del modello.
    if fit is not None and fit.scartato:
        parere.verdetto = "evita"
    return parere


if __name__ == "__main__":
    # Self-check della parte pura: il briefing sulle misure che il prompt riceve.
    # La chiamata LLM richiede ANTHROPIC_API_KEY e rete.
    from Ia_personal_shopper.models import FisicoUtente
    from Ia_personal_shopper.valutazione.fit import misure_target, valuta

    riccardo = ProfiloUtente(
        fisico=FisicoUtente(altezza_cm=194, larghezza_spalle_cm=58, circonferenza_petto_cm=102)
    )
    t_capo = misure_target(riccardo, "capospalla")
    t_top = misure_target(riccardo, "top")

    # Il cappotto Timberland reale: spalle 51 dichiarate, petto no
    timberland = MisureCapo(spalle_cm=51, lunghezza_cm=74, fonte="descrizione")
    sezione = _sezione_misure(valuta(timberland, t_capo, "capospalla"), timberland,
                              t_capo, "capospalla")
    assert "VA BENE" in sezione, sezione
    assert "NON bocciare" in sezione, "il modello deve smettere di ri-giudicare i centimetri"
    assert "petto" in sezione, "va detto quale misura resta ignota"

    # Capo fuori misura: il briefing deve dirlo senza ambiguità
    stretto = MisureCapo(spalle_cm=46, fonte="descrizione")
    sezione_no = _sezione_misure(valuta(stretto, t_capo, "capospalla"), stretto,
                                 t_capo, "capospalla")
    assert "NON VA BENE" in sezione_no, sezione_no

    # Nessuna misura: il briefing vieta di dedurre il fit dall'etichetta della taglia
    sezione_vuota = _sezione_misure(valuta(None, t_top, "top"), None, t_top, "top")
    assert "non dichiara nessuna misura" in sezione_vuota
    assert "da_chiedere" in sezione_vuota

    # Misure parziali su un top: manca una prioritaria, quindi nessuna certezza
    solo_lungh = MisureCapo(lunghezza_cm=72, fonte="descrizione")
    sezione_parz = _sezione_misure(valuta(solo_lungh, t_top, "top"), solo_lungh, t_top, "top")
    assert "non c'è certezza" in sezione_parz, sezione_parz
    assert "spalle" in sezione_parz

    print("OK")
