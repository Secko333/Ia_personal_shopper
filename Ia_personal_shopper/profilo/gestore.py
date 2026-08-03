"""Gestione persistente del profilo utente e dei preferiti."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from Ia_personal_shopper.config import GUARDAROBA_PATH, PREFERITI_PATH, PROFILO_PATH
from Ia_personal_shopper.models import (
    ArticoloPreferito,
    CapoGuardaroba,
    FisicoUtente,
    ListaGuardaroba,
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


# Vocabolari per separare quel che l'intervista aveva impastato in preferenze_stile.
# Il confronto è su voce intera, non per sottostringa: "slim rock" è uno stile, non la
# vestibilità "slim".
_VESTIBILITA_NOTE = {
    "aderente": "aderente", "slim": "aderente", "attillato": "aderente",
    "skinny": "aderente", "fit": "aderente", "stretto": "aderente",
    "regular": "regular", "normale": "regular", "classica": "regular",
    "oversize": "oversize", "largo": "oversize", "larga": "oversize", "boxy": "oversize",
    "comodo": "oversize", "comoda": "oversize", "ampio": "oversize", "ampia": "oversize",
}
_OCCASIONI_NOTE = {
    "serate", "serata", "sera", "tempo libero", "lavoro", "ufficio", "sport", "palestra",
    "cerimonia", "cerimonie", "viaggio", "viaggi", "università", "universita", "scuola",
    "aperitivo", "weekend", "casa", "vacanza", "vacanze",
}


def _migra_v2(profilo: ProfiloUtente) -> ProfiloUtente:
    """Separa stili, colori, occasioni e vestibilità finiti tutti in preferenze_stile.

    L'intervista di stile scriveva ogni risposta in un unico campo, quindi il vocabolario
    usato per costruire le query di ricerca conteneva anche "nero", "serate" e "aderente".
    Deterministico e senza rete: la migrazione gira al caricamento del profilo.
    """
    from Ia_personal_shopper.ricerca.interprete import COLOR_IDS  # import locale: il livello
    # profilo non deve dipendere da quello di ricerca a import time

    voci: list[str] = []
    for grezza in profilo.preferenze_stile:
        voci += [v.strip() for v in re.split(r"[;,]", grezza) if v.strip()]

    stili, colori, occasioni = [], [], []
    for voce in voci:
        chiave = voce.lower()
        if chiave in COLOR_IDS:
            colori.append(chiave)
        elif chiave in _OCCASIONI_NOTE:
            occasioni.append(chiave)
        elif chiave in _VESTIBILITA_NOTE:
            # Il profilo di partenza può contenerne più di una ("aderente" e "regular"):
            # vince la prima, correggibile con /profilo modifica.
            if profilo.vestibilita_preferita is None:
                profilo.vestibilita_preferita = _VESTIBILITA_NOTE[chiave]
        else:
            stili.append(voce)

    profilo.preferenze_stile = stili
    _append_dedup(profilo.colori_preferiti, colori)
    _append_dedup(profilo.occasioni, occasioni)
    profilo.versione = 2
    return profilo


def carica_profilo() -> ProfiloUtente:
    if not PROFILO_PATH.exists():
        profilo = profilo_default()
        salva_profilo(profilo)
        return profilo
    try:
        data = json.loads(PROFILO_PATH.read_text(encoding="utf-8"))
        profilo = ProfiloUtente.model_validate(data)
    except Exception:
        return profilo_default()

    if profilo.versione < 2:
        profilo = _migra_v2(profilo)
        salva_profilo(profilo)
    return profilo


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


def aggiorna_preferenze(
    stili: list[str] | None = None,
    colori: list[str] | None = None,
    occasioni: list[str] | None = None,
    vestibilita: str | None = None,
    da_evitare: list[str] | None = None,
) -> None:
    """Scrive ogni preferenza nel proprio campo (usata da /stile intervista).

    Tenerle separate all'origine è quel che impedisce a colori e occasioni di finire nel
    vocabolario di stile con cui si costruiscono le query di ricerca.
    """
    profilo = carica_profilo()
    if stili:
        _append_dedup(profilo.preferenze_stile, stili)
    if colori:
        _append_dedup(profilo.colori_preferiti, colori)
    if occasioni:
        _append_dedup(profilo.occasioni, occasioni)
    if vestibilita in ("aderente", "regular", "oversize"):
        profilo.vestibilita_preferita = vestibilita
    if da_evitare:
        _append_dedup(profilo.gusti_negativi, da_evitare)
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


# ---------------------------------------------------------------------------
# Guardaroba (capi posseduti)
# ---------------------------------------------------------------------------

def carica_guardaroba() -> ListaGuardaroba:
    if not GUARDAROBA_PATH.exists():
        return ListaGuardaroba()
    try:
        data = json.loads(GUARDAROBA_PATH.read_text(encoding="utf-8"))
        return ListaGuardaroba.model_validate(data)
    except Exception:
        return ListaGuardaroba()


def salva_guardaroba(lista: ListaGuardaroba) -> None:
    _scrivi_atomico(GUARDAROBA_PATH, lista.model_dump())


def aggiungi_capo(descrizione: str) -> str:
    lista = carica_guardaroba()
    capo = CapoGuardaroba(
        id=str(uuid.uuid4()),
        aggiunto_il=_ora_iso(),
        descrizione=descrizione.strip(),
    )
    lista.capi.append(capo)
    salva_guardaroba(lista)
    return capo.id


def rimuovi_capo(id_capo: str) -> bool:
    lista = carica_guardaroba()
    originale = len(lista.capi)
    lista.capi = [c for c in lista.capi if c.id != id_capo]
    if len(lista.capi) < originale:
        salva_guardaroba(lista)
        return True
    return False


if __name__ == "__main__":
    # Self-check della migrazione v1→v2: pura, nessuna rete, nessun file toccato.
    grezzo = ProfiloUtente(
        versione=1,
        preferenze_stile=[
            "Grunge; Alt-Rock; Modern Western; Indie; Slim Rock",
            "aderente", "regular", "nero", "bianco", "marrone", "blu", "verde",
            "serate", "tempo libero",
        ],
    )
    m = _migra_v2(grezzo)

    assert m.versione == 2
    # La stringa impastata dall'intervista diventa cinque stili distinti
    assert m.preferenze_stile == [
        "Grunge", "Alt-Rock", "Modern Western", "Indie", "Slim Rock"
    ], m.preferenze_stile
    # "Slim Rock" resta uno stile: il confronto è su voce intera, non per sottostringa
    assert "Slim Rock" in m.preferenze_stile
    assert m.colori_preferiti == ["nero", "bianco", "marrone", "blu", "verde"]
    assert m.occasioni == ["serate", "tempo libero"]
    # Due vestibilità in conflitto: vince la prima
    assert m.vestibilita_preferita == "aderente", m.vestibilita_preferita

    # Idempotente: rieseguirla non sposta nulla
    di_nuovo = _migra_v2(m.model_copy(deep=True))
    assert di_nuovo.preferenze_stile == m.preferenze_stile
    assert di_nuovo.colori_preferiti == m.colori_preferiti

    # Profilo già pulito o vuoto: nessun danno
    vuoto = _migra_v2(ProfiloUtente(versione=1))
    assert vuoto.preferenze_stile == [] and vuoto.vestibilita_preferita is None

    print("OK")
