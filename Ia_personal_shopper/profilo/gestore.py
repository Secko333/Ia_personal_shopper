"""Gestione persistente del profilo utente e dei preferiti."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from Ia_personal_shopper.config import PREFERITI_PATH, PROFILO_PATH
from Ia_personal_shopper.models import (
    ArticoloPreferito,
    FisicoUtente,
    ListaPreferiti,
    ProdottoRisultato,
    ProfiloUtente,
    TaglieUtente,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ora_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _scrivi_atomico(path: Path, data: dict) -> None:
    """Scrive JSON in modo atomico (temp file + rename) per evitare corruzione."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Profilo
# ---------------------------------------------------------------------------

def profilo_default() -> ProfiloUtente:
    return ProfiloUtente(
        nome="Riccardo",
        fisico=FisicoUtente(),
        taglie=TaglieUtente(),
        preferenze_stile=[],
        brand_esclusi=[],
        budget_default=100.0,
        siti_attivi=["zalando", "zara", "vinted"],
        aggiornato_il=_ora_iso(),
    )


def carica_profilo() -> ProfiloUtente:
    if not PROFILO_PATH.exists():
        profilo = profilo_default()
        salva_profilo(profilo)
        return profilo
    try:
        data = json.loads(PROFILO_PATH.read_text(encoding="utf-8"))
        return ProfiloUtente.model_validate(data)
    except Exception:
        return profilo_default()


def salva_profilo(profilo: ProfiloUtente) -> None:
    profilo.aggiornato_il = _ora_iso()
    _scrivi_atomico(PROFILO_PATH, profilo.model_dump())


# ---------------------------------------------------------------------------
# Preferiti
# ---------------------------------------------------------------------------

def carica_preferiti() -> ListaPreferiti:
    if not PREFERITI_PATH.exists():
        return ListaPreferiti()
    try:
        data = json.loads(PREFERITI_PATH.read_text(encoding="utf-8"))
        return ListaPreferiti.model_validate(data)
    except Exception:
        return ListaPreferiti()


def salva_preferiti(lista: ListaPreferiti) -> None:
    _scrivi_atomico(PREFERITI_PATH, lista.model_dump())


def aggiungi_preferito(prodotto: ProdottoRisultato, query_originale: str) -> str:
    lista = carica_preferiti()
    articolo = ArticoloPreferito(
        id=str(uuid.uuid4()),
        salvato_il=_ora_iso(),
        query_originale=query_originale,
        prodotto=prodotto,
    )
    lista.preferiti.append(articolo)
    salva_preferiti(lista)
    return articolo.id


def _append_dedup(lista: list[str], valori: list[str]) -> None:
    """Aggiunge valori a una lista mantenendo l'ordine ed evitando duplicati (case-insensitive)."""
    esistenti = {v.lower() for v in lista}
    for v in valori:
        v = v.strip()
        if v and v.lower() not in esistenti:
            lista.append(v)
            esistenti.add(v.lower())


def aggiungi_stile(descrittori: list[str]) -> None:
    profilo = carica_profilo()
    _append_dedup(profilo.preferenze_stile, descrittori)
    salva_profilo(profilo)


def aggiungi_gusti(positivi: list[str] | None = None, negativi: list[str] | None = None) -> None:
    profilo = carica_profilo()
    if positivi:
        _append_dedup(profilo.gusti_positivi, positivi)
    if negativi:
        _append_dedup(profilo.gusti_negativi, negativi)
    salva_profilo(profilo)


def rimuovi_preferito(id_articolo: str) -> bool:
    lista = carica_preferiti()
    originale = len(lista.preferiti)
    lista.preferiti = [p for p in lista.preferiti if p.id != id_articolo]
    if len(lista.preferiti) < originale:
        salva_preferiti(lista)
        return True
    return False
