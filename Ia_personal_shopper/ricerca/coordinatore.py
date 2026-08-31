"""Coordinatore: lancia ricerche in parallelo su tutti i siti attivi."""

from __future__ import annotations

import asyncio
import random

from rich.console import Console

from Ia_personal_shopper import vinted_api
from Ia_personal_shopper.browser import zalando, zara
from Ia_personal_shopper.browser.agente_base import crea_agente_ricerca
from Ia_personal_shopper.config import (
    DELAY_MAX_AGENTE,
    DELAY_MIN_AGENTE,
    MAX_CANDIDATI_FIT,
    MAX_CANDIDATI_GREZZI,
    MAX_CANDIDATI_STILE,
    MAX_STEPS_RICERCA,
)
from Ia_personal_shopper.models import (
    ParametriRicerca,
    ProdottoRisultato,
    ProfiloUtente,
    RisultatiRicerca,
)
from Ia_personal_shopper.profilo.gusti import affinita_gusto, vocabolari_gusto
from Ia_personal_shopper.ricerca.aggregatore import filtra_e_ordina
from Ia_personal_shopper.ricerca.interprete import color_ids, taglie_per_tipo

console = Console()

# Zara/Zalando usano browser-use; Vinted usa l'API JSON diretta (vedi _esegui_ricerca_sito).
_TASK_BUILDERS = {
    "zalando": zalando.build_task,
    "zara": zara.build_task,
}
_SITI_BROWSER = set(_TASK_BUILDERS)
_SITI_API = {"vinted"}

# Vinted catalog_ids per la sezione uomo/donna (verificato via API: 5=Uomo, 1904=Donna).
_CATALOG_ID_GENERE = {"uomo": "5", "donna": "1904"}

# search_text di Vinted cerca anche nelle descrizioni: aggiungere il vocabolario delle
# misure porta la quota di capi che le dichiarano dallo 0% al 75% (misurato il 2026-08-03
# su "maglietta manica corta" vs "t-shirt uomo misure spalle lunghezza"). Senza questo, il
# ranking per misure non ha niente da confrontare e ogni capo risulta "in forse".
_PAROLE_MISURE = {
    "top": "misure spalle lunghezza",
    # I capispalla si giudicano su spalle e petto, non sulla lunghezza (vedi fit.PESI):
    # senza una voce propria ricadevano su "misure", che da sola seleziona il 10% dei capi
    # con le misure dichiarate contro il 65% del vocabolario completo.
    "capospalla": "misure spalle petto",
    "pantaloni": "misure vita lunghezza",
}


def _migliori_per_gusto(
    prodotti: list[ProdottoRisultato], profilo: ProfiloUtente, limite: int
) -> list[ProdottoRisultato]:
    """Deduplica e tiene i `limite` capi più vicini ai gusti dell'utente.

    Leggere la pagina di un capo costa ~1,5s e Vinted frena chi ne legge troppe di fila: è
    la risorsa scarsa della ricerca, quindi va spesa sui capi giusti invece che sui primi
    che capitano. Titolo e brand bastano a scegliere — arrivano già nella risposta JSON,
    gratis — ed è lo stesso criterio che poi rompe i pari nel ranking finale
    (vedi valutazione/fit._chiave_ordine): qui decide solo chi merita la lettura.

    A pari affinità decide la rilevanza, che ogni ricerca assegna dalla propria posizione:
    così il primo capo della fetta "grunge" compete col primo della caccia alle misure
    invece di finire in fondo solo perché la sua ricerca è partita dopo.
    """
    positivi, negativi = vocabolari_gusto(profilo)
    unici: dict[str, ProdottoRisultato] = {}
    for p in prodotti:
        unici.setdefault(p.url, p)
    return sorted(
        unici.values(),
        key=lambda p: (-affinita_gusto(p, positivi, negativi), p.rilevanza),
    )[:limite]


def _cerca_vinted(
    params: ParametriRicerca,
    budget: float | None,
    profilo: ProfiloUtente,
) -> list[ProdottoRisultato]:
    """Caccia ai capi che dichiarano le misure, più una ricerca per ogni termine di stile.

    Il grosso del budget va alla query pura sulle misure: è quella che seleziona i venditori
    che le scrivono (65-75% dei risultati, contro lo 0% di una query neutra).

    Il gusto entra con UN termine per volta, in ricerche SEPARATE: la ricerca Vinted è
    un'intersezione, quindi sommare i termini nella stessa query fa crollare la quota di
    capi con le misure (misurato: "misure spalle lunghezza grunge" tiene il 65%, "misure
    spalle lunghezza band tee" scende al 10%). Query distinte coprono tre gusti diversi
    pagando su ognuna il prezzo di un solo termine.

    Le ricerche JSON sono gratis e vanno in fila (condividono la sessione curl_cffi
    module-level). Le descrizioni invece costano: si leggono dopo, e solo per i candidati
    selezionati per gusto — non per tutto il pescato.
    """
    comune = dict(
        taglie=taglie_per_tipo(params.tipo_capo, profilo),
        catalog_ids=_CATALOG_ID_GENERE.get(params.genere),
        color_ids=color_ids(params.colori),
        arricchisci_descrizioni=False,
    )
    parole = _PAROLE_MISURE.get(params.tipo_capo, "misure")
    base = f"{params.query} {parole}"

    # Senza termini di stile utilizzabili tutto il budget va alla caccia alle misure.
    termini = params.termini_stile
    quota_stile = MAX_CANDIDATI_STILE // len(termini) if termini else 0
    prodotti = vinted_api.cerca_vinted(
        base, budget, per_page=MAX_CANDIDATI_GREZZI - quota_stile * len(termini), **comune
    )
    for termine in termini:
        prodotti += vinted_api.cerca_vinted(
            f"{base} {termine}", budget, per_page=quota_stile, **comune
        )

    prodotti = _migliori_per_gusto(prodotti, profilo, MAX_CANDIDATI_FIT)
    vinted_api.arricchisci(prodotti)
    return prodotti


async def _esegui_ricerca_sito(
    sito: str,
    params: ParametriRicerca,
    budget: float | None,
    profilo: ProfiloUtente,
) -> list[ProdottoRisultato]:
    """Cerca su un singolo sito. Vinted via API JSON, gli altri via browser-use."""
    # Vinted: API JSON (veloce, gratis). curl_cffi è sincrono → gira in un thread.
    if sito == "vinted":
        return await asyncio.to_thread(_cerca_vinted, params, budget, profilo)

    # Delay casuale anti-bot per sfasare i lanci dei browser
    await asyncio.sleep(random.uniform(DELAY_MIN_AGENTE, DELAY_MAX_AGENTE))

    # I siti browser non hanno il filtro colore API: il colore torna nel testo di ricerca.
    query_browser = " ".join([params.query, *params.colori]).strip()
    builder = _TASK_BUILDERS[sito]
    task_str = builder(query_browser, budget, profilo, params.genere)

    agente = crea_agente_ricerca(sito, task_str)
    history = await agente.run(max_steps=MAX_STEPS_RICERCA)

    risultati: RisultatiRicerca | None = history.get_structured_output(RisultatiRicerca)
    if risultati is None:
        return []

    # Assicura che il campo sito sia corretto
    for p in risultati.prodotti:
        p.sito = sito

    return risultati.prodotti


async def cerca_su_tutti_i_siti(
    params: ParametriRicerca,
    budget: float | None,
    profilo: ProfiloUtente,
) -> list[ProdottoRisultato]:
    """
    Lancia ricerche in parallelo su tutti i siti attivi nel profilo.
    Se un sito fallisce, gli altri proseguono normalmente.
    """
    siti_attivi = [s for s in profilo.siti_attivi if s in _SITI_BROWSER or s in _SITI_API]

    tasks = [
        _esegui_ricerca_sito(sito, params, budget, profilo)
        for sito in siti_attivi
    ]

    risultati_per_sito = await asyncio.gather(*tasks, return_exceptions=True)

    tutti: list[ProdottoRisultato] = []
    for sito, risultato in zip(siti_attivi, risultati_per_sito):
        if isinstance(risultato, Exception):
            console.print(f"[yellow]⚠ {sito.capitalize()} non disponibile: {risultato}[/yellow]")
        else:
            console.print(f"[dim]✓ {sito.capitalize()}: {len(risultato)} prodotti trovati[/dim]")
            tutti.extend(risultato)

    return filtra_e_ordina(tutti, budget, profilo.brand_esclusi, limite=MAX_CANDIDATI_FIT)


if __name__ == "__main__":
    # Self-check offline della selezione dei candidati: è lei che decide di quali capi si
    # legge la pagina, cioè come si spende l'unica risorsa contingentata della ricerca.
    def _p(nome, rilevanza, url=None, brand=None):
        return ProdottoRisultato(
            nome=nome, url=url or nome, sito="vinted", brand=brand, rilevanza=rilevanza
        )

    utente = ProfiloUtente(preferenze_stile=["Grunge", "Modern Western"])

    misure = _p("T-shirt Hugo Boss uomo", 0)
    stile = _p("T-shirt grunge anni 90", 0)          # prima della SUA ricerca, non della lista
    stile_dopo = _p("Camicia western Lee", 3)
    fondo = _p("Polo Ralph Lauren", 1)

    scelti = _migliori_per_gusto([misure, fondo, stile, stile_dopo], utente, 3)
    # Il gusto decide chi merita la lettura; a pari gusto decide la rilevanza della sua fetta
    assert [p.nome for p in scelti] == [
        "T-shirt grunge anni 90", "Camicia western Lee", "T-shirt Hugo Boss uomo"
    ], [p.nome for p in scelti]

    # Lo stesso capo pescato da due ricerche si legge una volta sola: una pagina sprecata
    # è un capo in meno dentro il tetto di Vinted
    doppio = _migliori_per_gusto([stile, _p("altro titolo", 9, url=stile.url)], utente, 10)
    assert len(doppio) == 1, doppio

    # Profilo senza stili: nessuna affinità, quindi comanda la rilevanza e nulla si rompe
    assert [p.nome for p in _migliori_per_gusto([stile_dopo, misure], ProfiloUtente(), 2)] == [
        "T-shirt Hugo Boss uomo", "Camicia western Lee"
    ]
    assert _migliori_per_gusto([], utente, 5) == []

    print("OK")
