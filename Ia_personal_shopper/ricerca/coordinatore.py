"""Coordinatore: lancia ricerche in parallelo su tutti i siti attivi."""

from __future__ import annotations

import asyncio
import random

from rich.console import Console

from Ia_personal_shopper import vinted_api
from Ia_personal_shopper.browser import zalando, zara
from Ia_personal_shopper.browser.agente_base import crea_agente_ricerca
from Ia_personal_shopper.config import DELAY_MAX_AGENTE, DELAY_MIN_AGENTE, MAX_STEPS_RICERCA
from Ia_personal_shopper.models import ProdottoRisultato, ProfiloUtente, RisultatiRicerca
from Ia_personal_shopper.ricerca.aggregatore import filtra_e_ordina

console = Console()

# Zara/Zalando usano browser-use; Vinted usa l'API JSON diretta (vedi _esegui_ricerca_sito).
_TASK_BUILDERS = {
    "zalando": zalando.build_task,
    "zara": zara.build_task,
}
_SITI_BROWSER = set(_TASK_BUILDERS)
_SITI_API = {"vinted"}


async def _esegui_ricerca_sito(
    sito: str,
    query: str,
    budget: float | None,
    profilo: ProfiloUtente,
) -> list[ProdottoRisultato]:
    """Cerca su un singolo sito. Vinted via API JSON, gli altri via browser-use."""
    # Vinted: API JSON (veloce, gratis). curl_cffi è sincrono → gira in un thread.
    if sito == "vinted":
        taglie = [t for t in (profilo.taglie.top, profilo.taglie.pantaloni, profilo.taglie.scarpe) if t]
        return await asyncio.to_thread(
            vinted_api.cerca_vinted, query, budget, taglie=taglie or None
        )

    # Delay casuale anti-bot per sfasare i lanci dei browser
    await asyncio.sleep(random.uniform(DELAY_MIN_AGENTE, DELAY_MAX_AGENTE))

    builder = _TASK_BUILDERS[sito]
    task_str = builder(query, budget, profilo)

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
    query: str,
    budget: float | None,
    profilo: ProfiloUtente,
) -> list[ProdottoRisultato]:
    """
    Lancia ricerche in parallelo su tutti i siti attivi nel profilo.
    Se un sito fallisce, gli altri proseguono normalmente.
    """
    siti_attivi = [s for s in profilo.siti_attivi if s in _SITI_BROWSER or s in _SITI_API]

    tasks = [
        _esegui_ricerca_sito(sito, query, budget, profilo)
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

    return filtra_e_ordina(tutti, budget, profilo.brand_esclusi)
