"""Proposte proattive: Claude legge profilo + guardaroba e suggerisce capi/outfit da cercare."""

from __future__ import annotations

import json

import anthropic

from Ia_personal_shopper.config import MODELLO_VALUTAZIONE
from Ia_personal_shopper.models import (
    CapoGuardaroba,
    Proposta,
    ProfiloUtente,
    ProposteRicerca,
)
from Ia_personal_shopper.valutazione.consulente import costruisci_contesto_utente

_PROMPT_SISTEMA = (
    "Sei un personal shopper italiano che conosce a fondo il proprio cliente. "
    "Proponi di tua iniziativa capi e outfit che valorizzano il suo fisico, rispettano il suo stile "
    "e i suoi gusti, e completano il guardaroba che già possiede. Sii concreto e mai generico."
)


def _costruisci_prompt(profilo: ProfiloUtente, guardaroba: list[CapoGuardaroba]) -> str:
    contesto = costruisci_contesto_utente(profilo, guardaroba)
    return f"""Cliente: {profilo.nome}

{contesto}

Genera da 3 a 5 proposte di acquisto pensate apposta per questo cliente. Mescola:
- capi SINGOLI che completano o si abbinano a ciò che ha già nel guardaroba (colma i vuoti);
- almeno un OUTFIT completo (2-3 pezzi coordinati tra loro).

Per ogni proposta:
- titolo: breve e concreto (es. "Blazer beige per i tuoi chino scuri")
- motivo: perché la consigli a QUESTO cliente (abbinamento col guardaroba, valorizzazione fisico, gusto)
- ricerche: lista di query di ricerca in linguaggio naturale (1 per un capo singolo, 2-3 per un outfit).
  Ogni query deve descrivere il capo come lo scriveresti in una barra di ricerca (tipo, colore, stile).

Restituisci SOLO un JSON valido con questa struttura:
{{"proposte": [{{"titolo": "...", "motivo": "...", "ricerche": ["...", "..."]}}]}}"""


async def genera_proposte(
    profilo: ProfiloUtente,
    guardaroba: list[CapoGuardaroba],
) -> list[Proposta]:
    """Una singola chiamata Claude → lista di proposte. Lista vuota su errore."""
    client = anthropic.AsyncAnthropic()
    prompt = _costruisci_prompt(profilo, guardaroba)

    try:
        risposta = await client.messages.create(
            model=MODELLO_VALUTAZIONE,
            max_tokens=1500,
            system=_PROMPT_SISTEMA,
            messages=[{"role": "user", "content": prompt}],
        )
        testo = risposta.content[0].text.strip()
        inizio = testo.find("{")
        fine = testo.rfind("}") + 1
        if inizio >= 0 and fine > inizio:
            dati = json.loads(testo[inizio:fine])
            return ProposteRicerca.model_validate(dati).proposte
    except Exception:
        pass
    return []


if __name__ == "__main__":
    # Self-check live: gira solo con la chiave API presente (come ricerca/interprete.py).
    import asyncio
    import os

    from Ia_personal_shopper.models import FisicoUtente, TaglieUtente

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY assente — self-check saltato.")
    else:
        profilo = ProfiloUtente(
            nome="Test",
            fisico=FisicoUtente(altezza_cm=180, larghezza_spalle_cm=48),
            taglie=TaglieUtente(top="L", pantaloni="32"),
            preferenze_stile=["minimal", "toni neutri"],
        )
        guardaroba = [
            CapoGuardaroba(id="1", aggiunto_il="", descrizione="chino beige"),
            CapoGuardaroba(id="2", aggiunto_il="", descrizione="sneakers bianche in pelle"),
        ]
        proposte = asyncio.run(genera_proposte(profilo, guardaroba))
        assert proposte, "nessuna proposta generata"
        assert all(p.ricerche for p in proposte), "proposta senza ricerche"
        for p in proposte:
            print(f"• {p.titolo} — {p.motivo}")
            for q in p.ricerche:
                print(f"    → {q}")
        print(f"OK: {len(proposte)} proposte.")
