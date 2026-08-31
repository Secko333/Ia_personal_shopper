"""Valutazione editoriale dei prodotti trovati rispetto al profilo fisico dell'utente."""

from __future__ import annotations

import json

import anthropic

from Ia_personal_shopper.config import MODELLO_VALUTAZIONE
from Ia_personal_shopper.models import (
    CapoGuardaroba,
    MisureTarget,
    ProdottoArricchito,
    ProfiloUtente,
    ValutazioneProdotto,
    ValutazioniRicerca,
)

_PROMPT_SISTEMA = (
    "Sei un consulente di moda italiano esperto, onesto e diretto. "
    "Il tuo compito è valutare prodotti di abbigliamento per un utente specifico. "
    "Sii critico e obiettivo: se un prezzo è troppo alto dillo, se una taglia tende piccola dillo, "
    "se un articolo di seconda mano è in condizioni dubbie dillo. "
    "Non essere commerciale — l'utente si fida di te come di un amico esperto di moda."
)


def costruisci_contesto_utente(
    profilo: ProfiloUtente,
    guardaroba: list[CapoGuardaroba] | None = None,
    budget: float | None = None,
) -> str:
    """Blocco testuale con fisico, taglie, stile, gusti, guardaroba e budget.

    Condiviso tra valutazione prodotti (consulente) e proposte proattive (proposte.py).
    """
    fisico = profilo.fisico
    dati_fisici = []
    if fisico.altezza_cm:
        dati_fisici.append(f"Altezza: {fisico.altezza_cm}cm")
    if fisico.peso_kg:
        dati_fisici.append(f"Peso: {fisico.peso_kg}kg")
    if fisico.larghezza_spalle_cm:
        dati_fisici.append(f"Larghezza spalle: {fisico.larghezza_spalle_cm}cm")
    if fisico.circonferenza_petto_cm:
        dati_fisici.append(f"Petto: {fisico.circonferenza_petto_cm}cm")
    if fisico.circonferenza_vita_cm:
        dati_fisici.append(f"Vita: {fisico.circonferenza_vita_cm}cm")
    if fisico.circonferenza_fianchi_cm:
        dati_fisici.append(f"Fianchi: {fisico.circonferenza_fianchi_cm}cm")
    if fisico.note_corporatura:
        dati_fisici.append(f"Note: {fisico.note_corporatura}")

    taglie_info = []
    if profilo.taglie.top:
        taglie_info.append(f"Top/maglia: {profilo.taglie.top}")
    if profilo.taglie.pantaloni:
        taglie_info.append(f"Pantaloni: {profilo.taglie.pantaloni}")
    if profilo.taglie.scarpe:
        taglie_info.append(f"Scarpe: {profilo.taglie.scarpe}")

    stili = ", ".join(profilo.preferenze_stile) if profilo.preferenze_stile else "non specificato"
    colori_pref = ", ".join(profilo.colori_preferiti) if profilo.colori_preferiti else "nessuna preferenza"
    occasioni = ", ".join(profilo.occasioni) if profilo.occasioni else "non specificate"
    vestibilita_pref = profilo.vestibilita_preferita or "non specificata"
    budget_str = f"{budget:.0f}€" if budget else f"{profilo.budget_default:.0f}€ (default)"

    sezione_fisico = "\n".join(dati_fisici) if dati_fisici else "Non specificato"
    sezione_taglie = "\n".join(taglie_info) if taglie_info else "Non specificato"

    gusti_pos = ", ".join(profilo.gusti_positivi) if profilo.gusti_positivi else "nessuno registrato"
    gusti_neg = ", ".join(profilo.gusti_negativi) if profilo.gusti_negativi else "nessuno registrato"

    # Guardaroba: capi che l'utente già possiede → serve per abbinamenti e stile coerente.
    # Troncato a 30 capi per non gonfiare il prompt.
    capi = guardaroba or []
    if capi:
        sezione_guardaroba = "\n".join(f"- {c.descrizione}" for c in capi[:30])
    else:
        sezione_guardaroba = "Vuoto (nessun capo registrato)"

    return f"""PROFILO FISICO:
{sezione_fisico}

TAGLIE ABITUALI:
{sezione_taglie}

STILE PREFERITO: {stili}
COLORI PREFERITI: {colori_pref}
OCCASIONI D'USO: {occasioni}
VESTIBILITÀ PREFERITA: {vestibilita_pref}
GUSTI APPRESI — PIACCIONO: {gusti_pos}
GUSTI APPRESI — DA EVITARE: {gusti_neg}
GUARDAROBA ATTUALE (capi già posseduti):
{sezione_guardaroba}
BUDGET MASSIMO: {budget_str}"""


def _costruisci_prompt_utente(
    arricchiti: list[ProdottoArricchito],
    profilo: ProfiloUtente,
    budget: float | None,
    guardaroba: list[CapoGuardaroba] | None = None,
    target: MisureTarget | None = None,
) -> str:
    contesto = costruisci_contesto_utente(profilo, guardaroba, budget)

    # Il confronto sulle misure è già stato fatto in valutazione/fit.py: qui arriva come
    # dato ("fit"), non come compito. Al modello resta il giudizio su prezzo, stile e
    # abbinamenti. "indice" (1..N) è la chiave di matching: più robusto degli URL lunghi.
    prodotti_dump = []
    for i, pa in enumerate(arricchiti, start=1):
        d = pa.prodotto.model_dump(exclude={"foto"})
        d["indice"] = i
        if d.get("descrizione"):
            d["descrizione"] = d["descrizione"][:400]
        if pa.fit is not None:
            d["fit"] = pa.fit.dettaglio
        if pa.misure is not None:
            d["misure_dichiarate_in"] = pa.misure.fonte
        prodotti_dump.append(d)
    lista_prodotti = json.dumps(prodotti_dump, ensure_ascii=False, indent=2)

    sezione_target = ""
    if target is not None:
        from Ia_personal_shopper.valutazione.fit import (
            SCARTO_MAX_CM,
            SCARTO_MAX_SECONDARIO_CM,
            descrivi_target,
        )
        descrizione_target = descrivi_target(target)
        if descrizione_target:
            # Il verdetto sulle misure è già stato preso in modo deterministico: i capi fuori
            # tolleranza non sono nemmeno in questa lista. Senza dirlo, il modello ri-giudicava
            # i centimetri e bocciava capi che la riga fit dichiarava a misura — due messaggi
            # contraddittori nella stessa riga di tabella.
            sezione_target = (
                f"\nMISURE CERCATE PER QUESTO CAPO: {descrizione_target}\n"
                f"Il campo \"fit\" è già il confronto fatto: i capi fuori tolleranza sono stati "
                f"esclusi prima di arrivare a te. Tolleranze accettate dall'utente: fino a "
                f"{SCARTO_MAX_CM:.0f}cm su lunghezza e spalle, fino a "
                f"{SCARTO_MAX_SECONDARIO_CM:.0f}cm sul petto, che per lui conta meno.\n"
                "Quindi NON bocciare un capo per uno scarto che il campo \"fit\" riporta: quello "
                "scarto è già stato giudicato accettabile. Puoi menzionarlo, ma il tuo verdetto "
                "deve basarsi su prezzo, condizioni, stile e abbinamenti.\n"
                "\"misure non dichiarate\" significa che il venditore non le ha scritte: il capo "
                "potrebbe andare bene o no, dillo nel commento e sii più prudente.\n"
            )

    return f"""Valuta questi prodotti per l'utente {profilo.nome}:

{contesto}
{sezione_target}
PRODOTTI DA VALUTARE:
{lista_prodotti}

Per ogni prodotto fornisci:
- indice: il numero del prodotto (copia il campo "indice" dal JSON sopra)
- si_adatta_fisico: true/false — se il capo si adatta al fisico e allo stile dell'utente E si abbina bene ai capi già nel GUARDAROBA ATTUALE. Usa il campo "fit" come prova principale.
- ottimo_affare: true/false — se il prezzo è buono rispetto al mercato
- commento: massimo 12 parole, diretto e onesto (es. "Ottimo prezzo, misure perfette per te", "Si abbina ai tuoi chino scuri", "Misure non dichiarate: chiedile al venditore"). Tieni conto dei gusti appresi e degli abbinamenti col guardaroba.
- raccomandazione: "compra" | "considera" | "evita"

Prodotti Vinted di seconda mano: considera la condizione nel prezzo.

Restituisci SOLO un JSON valido con questa struttura:
{{"valutazioni": [{{"indice": 1, "si_adatta_fisico": true, "ottimo_affare": true, "commento": "...", "raccomandazione": "compra"}}]}}"""


async def valuta_prodotti(
    arricchiti: list[ProdottoArricchito],
    profilo: ProfiloUtente,
    budget: float | None,
    guardaroba: list[CapoGuardaroba] | None = None,
    target: MisureTarget | None = None,
) -> list[ProdottoArricchito]:
    """Riempie il campo `valutazione` di ogni prodotto con una singola chiamata Claude.

    I campi `misure` e `fit` sono già stati calcolati da valutazione/fit.py e vengono
    passati al modello come dato di partenza, non ricalcolati.
    """
    if not arricchiti:
        return []

    client = anthropic.AsyncAnthropic()
    prompt = _costruisci_prompt_utente(arricchiti, profilo, budget, guardaroba, target)

    try:
        risposta = await client.messages.create(
            model=MODELLO_VALUTAZIONE,
            max_tokens=4096,  # 20 prodotti × ~80 token di JSON: 2048 troncava e perdeva tutto
            system=_PROMPT_SISTEMA,
            messages=[{"role": "user", "content": prompt}],
        )

        testo = risposta.content[0].text.strip()
        # Estrai JSON anche se c'è testo aggiuntivo intorno
        inizio = testo.find("{")
        fine = testo.rfind("}") + 1
        if inizio >= 0 and fine > inizio:
            testo_json = testo[inizio:fine]
            dati = json.loads(testo_json)
            valutazioni_raw = ValutazioniRicerca.model_validate(dati)
        else:
            valutazioni_raw = ValutazioniRicerca()

    except Exception:
        # In caso di errore della valutazione, restituiamo i prodotti senza valutazione
        valutazioni_raw = ValutazioniRicerca()

    # Mappa indice (1..N) → valutazione
    mappa_val: dict[int, ValutazioneProdotto] = {
        v.indice: v for v in valutazioni_raw.valutazioni
    }

    for i, pa in enumerate(arricchiti, start=1):
        pa.valutazione = mappa_val.get(i)
    return arricchiti
