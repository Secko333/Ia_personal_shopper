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
    MAX_STEPS_RICERCA,
)
from Ia_personal_shopper.models import (
    ParametriRicerca,
    ProdottoRisultato,
    ProfiloUtente,
    RisultatiRicerca,
)
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
    "pantaloni": "misure vita lunghezza",
}


def _cerca_vinted(
    params: ParametriRicerca,
    budget: float | None,
    profilo: ProfiloUtente,
) -> list[ProdottoRisultato]:
    """Una ricerca per variante di gusto, più quella neutra, unite in un solo pool.

    Le varianti di gusto vanno per prime: su una query neutra Vinted restituisce il
    mainstream (Hugo Boss, Disney, Guess), quindi nessun riordino a valle può produrre una
    lista di gusto — bisogna pescare diverso. L'ordine determina anche la `rilevanza`, che
    è lo spareggio quando le misure non discriminano, così i capi di gusto vincono i pari.

    Il budget di candidati è ripartito tra le varianti: nessun costo in più, cambia solo da
    dove arrivano. Sequenziali e non parallele: condividono la sessione curl_cffi.
    """
    comune = dict(
        taglie=taglie_per_tipo(params.tipo_capo, profilo),
        catalog_ids=_CATALOG_ID_GENERE.get(params.genere),
        color_ids=color_ids(params.colori),
    )
    parole = _PAROLE_MISURE.get(params.tipo_capo, "misure")
    # Solo varianti di gusto quando ci sono: la query neutra portava esclusivamente
    # mainstream (Hugo Boss, Disney, Tommy, Liu Jo) e, avendo spesso misure complete e fit
    # regolare, occupava le prime posizioni. Le varianti da sole danno ~58 candidati unici,
    # quindi il pool non si assottiglia. Se l'interprete non ne genera — richiesta già
    # specifica, es. "t-shirt dei Ramones" — la neutra riprende tutto il budget.
    varianti = params.varianti_gusto or [params.query]
    quota = max(MAX_CANDIDATI_FIT // len(varianti), 10)

    prodotti: list[ProdottoRisultato] = []
    for variante in varianti:
        prodotti += vinted_api.cerca_vinted(
            f"{variante} {parole}", budget, per_page=quota, **comune
        )

    # Rilevanza riassegnata sull'ordine unito: ogni chiamata la numera da 0.
    for posizione, p in enumerate(prodotti):
        p.rilevanza = posizione
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
