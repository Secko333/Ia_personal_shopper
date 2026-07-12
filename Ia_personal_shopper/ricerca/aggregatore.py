"""Deduplica, filtra per budget e ordina i risultati aggregati da più siti."""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

from Ia_personal_shopper.config import MAX_RISULTATI_TOTALI
from Ia_personal_shopper.models import ProdottoRisultato


def _normalizza_url(url: str) -> str:
    """Rimuove query params e frammenti per deduplicare URL."""
    try:
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", "")).lower().rstrip("/")
    except Exception:
        return url.lower().strip()


def _chiave_deduplica(p: ProdottoRisultato) -> str:
    return _normalizza_url(p.url)


def filtra_e_ordina(
    prodotti: list[ProdottoRisultato],
    budget: float | None,
    brand_esclusi: list[str] | None = None,
) -> list[ProdottoRisultato]:
    """
    1. Deduplica per URL normalizzato.
    2. Filtra per brand esclusi.
    3. Filtra per budget (prodotti senza prezzo vengono tenuti ma posizionati in fondo).
    4. Ordina per prezzo crescente (None in fondo).
    5. Tronca a MAX_RISULTATI_TOTALI.
    """
    brand_esclusi_lower = {b.lower() for b in (brand_esclusi or [])}

    # Deduplica
    visti: set[str] = set()
    unici: list[ProdottoRisultato] = []
    for p in prodotti:
        chiave = _chiave_deduplica(p)
        if chiave not in visti:
            visti.add(chiave)
            unici.append(p)

    # Filtra brand esclusi
    if brand_esclusi_lower:
        unici = [
            p for p in unici
            if not p.brand or p.brand.lower() not in brand_esclusi_lower
        ]

    # Filtra budget: rimuove solo prodotti con prezzo noto > budget
    if budget:
        unici = [
            p for p in unici
            if p.prezzo is None or p.prezzo <= budget
        ]

    # Ordina: prezzi noti crescenti, poi quelli senza prezzo
    con_prezzo = sorted([p for p in unici if p.prezzo is not None], key=lambda p: p.prezzo)  # type: ignore[arg-type]
    senza_prezzo = [p for p in unici if p.prezzo is None]

    return (con_prezzo + senza_prezzo)[:MAX_RISULTATI_TOTALI]


def estrai_budget(testo: str) -> float | None:
    """Estrae il budget massimo da un testo libero in italiano."""
    pattern = re.compile(
        r"(?:max(?:imo)?|meno di|entro|budget|fino a|sotto)\s*[:i]?\s*(\d+(?:[.,]\d+)?)\s*€?",
        re.IGNORECASE,
    )
    match = pattern.search(testo)
    if match:
        valore = match.group(1).replace(",", ".")
        return float(valore)

    # Fallback: cerca pattern "N€" o "€N" standalone
    pattern2 = re.compile(r"(\d+(?:[.,]\d+)?)\s*€", re.IGNORECASE)
    match2 = pattern2.search(testo)
    if match2:
        return float(match2.group(1).replace(",", "."))

    return None
