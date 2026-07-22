"""Client per l'API JSON interna di Vinted (sostituisce browser-use per Vinted).

Usa curl_cffi con impersonate Chrome per un fingerprint TLS/JA3 realistico, così DataDome
non blocca le richieste. Per uso personale a basso volume non servono proxy.

Pattern: si "scalda" una sessione (GET homepage → cookie _vinted_fr_session + DataDome),
poi si interroga /api/v2/catalog/items. Su 403 si ri-scalda e si ritenta una volta.
"""

from __future__ import annotations

import html as html_lib
import re
import time

from curl_cffi import requests

from Ia_personal_shopper.config import BROWSER_DATA_DIR, MAX_RISULTATI_PER_SITO
from Ia_personal_shopper.models import ProdottoRisultato

_META_DESC = re.compile(r'<meta name="description" content="([^"]*)"')

BASE = "https://www.vinted.it"
ENDPOINT_ITEMS = f"{BASE}/api/v2/catalog/items"

# Cookie di autenticazione Vinted (JWT-based): se presenti nel profilo browser persistente
# di /carrello, identificano l'utente loggato. Riusarli qui rende autenticate anche le ricerche.
_COOKIE_AUTH = {"access_token_web", "refresh_token_web"}

# ponytail: sessione module-level riusata tra ricerche; ri-warm solo su 403.
_session = None


def _cookie_da_browser() -> list[dict] | None:
    """Legge i cookie di sessione dal profilo Chromium persistente usato da /carrello.

    Se l'utente si è loggato manualmente in quella finestra (browser-use, headful),
    i cookie restano salvati su disco: li leggiamo con un contesto Playwright headless
    e li trapiantiamo nella sessione curl_cffi per autenticare anche le ricerche via API.
    Nessuna password è letta o salvata: solo i cookie di sessione già presenti sul disco.
    """
    profilo_dir = BROWSER_DATA_DIR / "vinted"
    if not profilo_dir.exists():
        return None
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(str(profilo_dir), headless=True)
            try:
                return context.cookies("https://www.vinted.it")
            finally:
                context.close()
    except Exception:
        # Profilo assente, in uso da un altro processo, o Playwright non disponibile:
        # si prosegue in modalità anonima.
        return None


def _warm():
    """Crea una sessione con fingerprint Chrome e ottiene i cookie di Vinted/DataDome."""
    s = requests.Session(impersonate="chrome")
    s.get(BASE, timeout=15)  # popola _vinted_fr_session + cookie DataDome

    for cookie in _cookie_da_browser() or []:
        if cookie.get("name") in _COOKIE_AUTH:
            s.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain") or ".vinted.it")

    return s


def _get_session(force: bool = False):
    global _session
    if _session is None or force:
        _session = _warm()
    return _session


def _prezzo(valore) -> float | None:
    """L'API restituisce il prezzo come stringa o come oggetto {'amount': '12.0', ...}."""
    if valore is None:
        return None
    if isinstance(valore, dict):
        valore = valore.get("amount")
    try:
        return float(valore)
    except (TypeError, ValueError):
        return None


def _mappa_item(item: dict) -> ProdottoRisultato:
    photo = item.get("photo") or {}
    # Prezzo base ("price") prima del totale con protezione acquisti: è quello su cui
    # filtra price_to, così il prezzo mostrato è coerente col budget.
    prezzo = _prezzo(item.get("price")) or _prezzo(item.get("total_item_price"))
    return ProdottoRisultato(
        nome=item.get("title") or "(senza titolo)",
        brand=item.get("brand_title"),
        prezzo=prezzo,
        taglia_disponibile=item.get("size_title"),
        url=item.get("url") or f"{BASE}/items/{item.get('id')}",
        sito="vinted",
        immagine_url=photo.get("url"),
        condizione=item.get("status"),
        descrizione=item.get("description"),  # None dalla lista → arricchito da _descrizione_da_pagina
    )


def _descrizione_da_pagina(session, url: str) -> str | None:
    """Estrae la descrizione del venditore (con le misure del capo) dal meta tag della pagina.

    L'endpoint API di dettaglio è protetto da DataDome (403), ma la pagina pubblica no:
    il <meta name="description"> contiene il testo del venditore, misure incluse.
    """
    try:
        r = session.get(url, timeout=20)
        if r.status_code == 200:
            m = _META_DESC.search(r.text)
            if m:
                return html_lib.unescape(m.group(1))
    except Exception:
        pass
    return None


# Token alfabetici che contano come taglia (le altre parole del profilo, es. "di vita", si ignorano).
_TAGLIE_ALPHA = {"xs", "s", "m", "l", "xl", "xxl", "xxxl"}


def _taglia_compatibile(size_title: str | None, taglie: list[str]) -> bool:
    """Filtro morbido: True se la taglia del capo matcha una taglia utente o è assente.

    Entrambi i lati sono ridotti a token atomici ("W32 | IT 46" → w, 32, it, 46), così
    "32" matcha "W32" e il testo libero del profilo ("32 di vita 36/38") non scarta tutto.
    """
    if not size_title:
        return True
    tokens_capo = set(re.findall(r"[a-z]+|\d+", size_title.lower()))
    for taglia in taglie:
        for tok in re.findall(r"[a-z]+|\d+", (taglia or "").lower()):
            if (tok.isdigit() or tok in _TAGLIE_ALPHA) and tok in tokens_capo:
                return True
    return False


def cerca_vinted(
    query: str,
    budget: float | None = None,
    per_page: int = MAX_RISULTATI_PER_SITO,
    order: str = "relevance",
    arricchisci_descrizioni: bool = True,
    taglie: list[str] | None = None,
    escludi_condizione_scarsa: bool = False,
    catalog_ids: str | None = None,
    color_ids: str | None = None,
) -> list[ProdottoRisultato]:
    """Cerca articoli su Vinted via API JSON. Ritorna al massimo `per_page` prodotti.

    taglie: filtro morbido post-fetch sulle taglie utente (i capi senza taglia passano).
    escludi_condizione_scarsa: scarta gli articoli in stato "Soddisfacente".
    catalog_ids: ID categoria Vinted (es. uomo) passato all'API, opzionale.
    color_ids: ID colore Vinted in CSV (vedi ricerca/interprete.py), opzionale.
    """
    # I filtri post-fetch riducono i risultati → si chiede una pagina più larga e si tronca dopo.
    filtri_attivi = bool(taglie or escludi_condizione_scarsa)
    params = {
        "search_text": query,
        "per_page": max(per_page * 3, 24) if filtri_attivi else per_page,
        "order": order,
        "currency": "EUR",
    }
    if budget:
        params["price_to"] = int(budget)
    if catalog_ids:
        params["catalog_ids"] = catalog_ids
    if color_ids:
        params["color_ids"] = color_ids

    session = _get_session()
    r = session.get(ENDPOINT_ITEMS, params=params, timeout=20)
    if r.status_code == 403:
        session = _get_session(force=True)  # cookie scaduto → ri-warm e ritenta
        r = session.get(ENDPOINT_ITEMS, params=params, timeout=20)
    r.raise_for_status()

    items = (r.json() or {}).get("items", [])
    prodotti = [_mappa_item(it) for it in items]

    if taglie:
        prodotti = [p for p in prodotti if _taglia_compatibile(p.taglia_disponibile, taglie)]
    if escludi_condizione_scarsa:
        prodotti = [p for p in prodotti if (p.condizione or "").lower() != "soddisfacente"]
    prodotti = prodotti[:per_page]  # tronca PRIMA dell'arricchimento: max per_page GET

    # ponytail: descrizione (per il fit) presa dal meta della pagina pubblica, una GET per prodotto.
    # Ceiling: N pagine HTML (~2MB l'una). Bounded a per_page; se pesa, ridurre ai top-3.
    if arricchisci_descrizioni:
        for prod in prodotti:
            prod.descrizione = _descrizione_da_pagina(session, prod.url)
            time.sleep(0.3)  # ritmo gentile anti-bot

    return prodotti


if __name__ == "__main__":
    # Self-check: richiede rete e un IP non bloccato da DataDome.
    assert _taglia_compatibile("M / IT 48 / EU 44", ["M"])
    assert _taglia_compatibile("IT 42", ["42"])
    assert _taglia_compatibile(None, ["M"])
    assert not _taglia_compatibile("12 anni / 152 cm", ["M", "32", "42"])
    assert _taglia_compatibile("W32 | IT 46", ["32 di vita 36/38 di lunghezza"])
    assert not _taglia_compatibile("W28 | IT 42", ["32 di vita 36/38 di lunghezza"])
    assert not _taglia_compatibile("Taglia unica", ["l", "32 di vita 36/38 di lunghezza", "47"])

    risultati = cerca_vinted("nike", budget=40, arricchisci_descrizioni=False)
    for p in risultati[:5]:
        print(f"- {p.nome} | {p.brand} | €{p.prezzo} | {p.taglia_disponibile} | {p.condizione}")
    assert len(risultati) > 0, "Nessun risultato: possibile blocco DataDome o endpoint cambiato"
    assert all(p.prezzo is None or p.prezzo <= 40 for p in risultati), "price_to non rispettato"

    con_taglia = cerca_vinted("felpa nike", budget=40, arricchisci_descrizioni=False, taglie=["M"])
    for p in con_taglia[:5]:
        print(f"- [taglia M] {p.nome} | {p.taglia_disponibile}")
    assert all(_taglia_compatibile(p.taglia_disponibile, ["M"]) for p in con_taglia)
    print(f"\nOK: {len(risultati)} prodotti, {len(con_taglia)} con filtro taglia M")
