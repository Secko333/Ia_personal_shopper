"""Valutazione editoriale dei prodotti trovati rispetto al profilo fisico dell'utente."""

from __future__ import annotations

import json

import anthropic

from Ia_personal_shopper.config import MODELLO_VALUTAZIONE
from Ia_personal_shopper.models import (
    ProdottoArricchito,
    ProdottoRisultato,
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


def _costruisci_prompt_utente(
    prodotti: list[ProdottoRisultato],
    profilo: ProfiloUtente,
    budget: float | None,
) -> str:
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
    budget_str = f"{budget:.0f}€" if budget else f"{profilo.budget_default:.0f}€ (default)"

    sezione_fisico = "\n".join(dati_fisici) if dati_fisici else "Non specificato"
    sezione_taglie = "\n".join(taglie_info) if taglie_info else "Non specificato"

    gusti_pos = ", ".join(profilo.gusti_positivi) if profilo.gusti_positivi else "nessuno registrato"
    gusti_neg = ", ".join(profilo.gusti_negativi) if profilo.gusti_negativi else "nessuno registrato"

    # Includiamo la descrizione (venditore Vinted): spesso contiene le misure reali del capo.
    # Troncata a 400 caratteri per non gonfiare il prompt su molti prodotti.
    # "indice" (1..N) è la chiave di matching: più robusto che ricopiare URL lunghissimi.
    prodotti_dump = []
    for i, p in enumerate(prodotti, start=1):
        d = p.model_dump()
        d["indice"] = i
        if d.get("descrizione"):
            d["descrizione"] = d["descrizione"][:400]
        prodotti_dump.append(d)
    lista_prodotti = json.dumps(prodotti_dump, ensure_ascii=False, indent=2)

    return f"""Valuta questi prodotti per l'utente {profilo.nome}:

PROFILO FISICO:
{sezione_fisico}

TAGLIE ABITUALI:
{sezione_taglie}

STILE PREFERITO: {stili}
GUSTI APPRESI — PIACCIONO: {gusti_pos}
GUSTI APPRESI — DA EVITARE: {gusti_neg}
BUDGET MASSIMO: {budget_str}

PRODOTTI DA VALUTARE:
{lista_prodotti}

Per ogni prodotto fornisci:
- indice: il numero del prodotto (copia il campo "indice" dal JSON sopra)
- si_adatta_fisico: true/false — se il capo si adatta al fisico e allo stile dell'utente
- ottimo_affare: true/false — se il prezzo è buono rispetto al mercato
- commento: massimo 12 parole, diretto e onesto (es. "Ottimo prezzo, L ti starà bene", "Troppo caro per quello che è"). Tieni conto dei gusti appresi.
- raccomandazione: "compra" | "considera" | "evita"
- vestibilita: SE il campo "descrizione" del prodotto riporta misure del capo (es. spalle, petto/pit-to-pit, lunghezza, girovita), estraile e confrontale con le misure corporee dell'utente, poi dai un verdetto breve (es. "spalle 46cm vs tue 48 → stretto", "petto 56cm → vestibilità comoda"). Se la descrizione NON riporta misure, imposta vestibilita a null e valuta il fit solo da taglia+stile.

Prodotti Vinted di seconda mano: considera la condizione nel prezzo.

Restituisci SOLO un JSON valido con questa struttura:
{{"valutazioni": [{{"indice": 1, "si_adatta_fisico": true, "ottimo_affare": true, "commento": "...", "raccomandazione": "compra", "vestibilita": null}}]}}"""


async def valuta_prodotti(
    prodotti: list[ProdottoRisultato],
    profilo: ProfiloUtente,
    budget: float | None,
) -> list[ProdottoArricchito]:
    """Valuta tutti i prodotti in una singola chiamata Claude e li arricchisce con le opinioni."""
    if not prodotti:
        return []

    client = anthropic.AsyncAnthropic()
    prompt = _costruisci_prompt_utente(prodotti, profilo, budget)

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

    return [
        ProdottoArricchito(
            prodotto=p,
            valutazione=mappa_val.get(i),
        )
        for i, p in enumerate(prodotti, start=1)
    ]
