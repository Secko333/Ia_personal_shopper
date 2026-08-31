"""Client per l'API JSON interna di Vinted (sostituisce browser-use per Vinted).

Usa curl_cffi con impersonate Chrome per un fingerprint TLS/JA3 realistico, così DataDome
non blocca le richieste. Per uso personale a basso volume non servono proxy.

Pattern: si "scalda" una sessione (GET homepage → cookie _vinted_fr_session + DataDome),
poi si interroga /api/v2/catalog/items. Su 403 si ri-scalda e si ritenta una volta.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from concurrent.futures import ThreadPoolExecutor

from curl_cffi import requests

from Ia_personal_shopper.config import (
    BROWSER_DATA_DIR,
    DESCRIZIONI_PARALLELE,
    MAX_RISULTATI_PER_SITO,
)
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


# I token letti dal Chrome dell'utente si tengono in cache per tutta la durata del processo:
# ogni lettura fa comparire il dialogo del Keychain, e _warm() viene richiamata a ogni 403.
_cookie_chrome: dict[str, str] | None = None


def _auth_da_chrome() -> dict[str, str]:
    """Token di sessione dal Chrome dell'utente, letti una sola volta per processo."""
    global _cookie_chrome
    if _cookie_chrome is None:
        try:
            from Ia_personal_shopper.browser.cookie_chrome import cookie_vinted
            _cookie_chrome = cookie_vinted()
        except Exception:
            _cookie_chrome = {}
    return _cookie_chrome


def _warm():
    """Crea una sessione con fingerprint Chrome e ottiene i cookie di Vinted/DataDome.

    L'autenticazione si prende dal Chrome dell'utente se è loggato là — nessun browser da
    lanciare, quindi niente da far riconoscere a DataDome. Il profilo browser persistente
    di /login resta come ripiego per chi non usa Chrome.
    """
    s = requests.Session(impersonate="chrome")
    s.get(BASE, timeout=15)  # popola _vinted_fr_session + cookie DataDome

    auth = dict(_auth_da_chrome())
    if not auth:
        for cookie in _cookie_da_browser() or []:
            if cookie.get("name") in _COOKIE_AUTH:
                auth[cookie["name"]] = cookie["value"]

    for nome, valore in auth.items():
        s.cookies.set(nome, valore, domain=".vinted.it")

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
    # "photos" arriva già nella risposta della lista (5-15 URL full-size, zero GET extra):
    # è la fonte del fallback vision quando le misure non sono nella descrizione.
    foto = [f["url"] for f in (item.get("photos") or []) if f.get("url")]
    return ProdottoRisultato(
        nome=item.get("title") or "(senza titolo)",
        brand=item.get("brand_title"),
        prezzo=prezzo,
        taglia_disponibile=item.get("size_title"),
        url=item.get("url") or f"{BASE}/items/{item.get('id')}",
        sito="vinted",
        immagine_url=photo.get("url"),
        condizione=item.get("status"),
        descrizione=item.get("description"),  # None dalla lista → riempito da arricchisci()
        foto=foto,
    )


# ---------------------------------------------------------------------------
# Feed personalizzato della homepage
# ---------------------------------------------------------------------------

# La homepage è renderizzata lato server: i capi del feed arrivano come oggetti JSON
# completi dentro i payload Next.js, senza bisogno di un endpoint API. Il feed vero
# (paginato) passa da /web/gateway/homepage/homepage, che senza l'autenticazione giusta
# risponde BAD_REQUEST: da qui si leggono quindi i primi 20 capi e basta.
# ponytail: 20 capi per fetch, non paginabili. Se servisse di più, la strada è il gateway.
_PUSH_NEXT = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_ENTITY_ITEM = re.compile(r'"type":"item","entity":\{')


def _oggetto_json(testo: str, inizio: int) -> str | None:
    """Ritaglia l'oggetto JSON che comincia a `inizio`, bilanciando le graffe.

    Non basta cercare la prima '}': i titoli dei venditori contengono graffe e virgolette.
    """
    profondita, in_stringa, escape = 0, False, False
    for i in range(inizio, len(testo)):
        c = testo[i]
        if escape:
            escape = False
        elif c == "\\":
            escape = True
        elif c == '"':
            in_stringa = not in_stringa
        elif not in_stringa:
            if c == "{":
                profondita += 1
            elif c == "}":
                profondita -= 1
                if profondita == 0:
                    return testo[inizio:i + 1]
    return None


def _prodotto_da_entity(e: dict) -> ProdottoRisultato | None:
    """Converte un'entity del feed in ProdottoRisultato.

    secondLine dell'itemBox è "M / IT 42 / EU 38 · Ottime": taglia e condizione insieme.
    Il brand non è presente nel feed; resta None e il consulente ne fa a meno.
    """
    url = e.get("url") or ""
    if not url:
        return None
    taglia = condizione = None
    seconda = (e.get("itemBox") or {}).get("secondLine") or ""
    if seconda:
        pezzi = [p.strip() for p in seconda.split("·")]
        taglia = pezzi[0] or None
        condizione = pezzi[1] if len(pezzi) > 1 else None

    return ProdottoRisultato(
        nome=e.get("title") or "(senza titolo)",
        prezzo=_prezzo(e.get("price")),
        taglia_disponibile=taglia,
        url=url if url.startswith("http") else BASE + url,
        sito="vinted",
        immagine_url=e.get("thumbnailUrl"),
        condizione=condizione,
        foto=[u for u in [e.get("thumbnailUrl")] if u],
    )


def leggi_feed(arricchisci_descrizioni: bool = True) -> list[ProdottoRisultato]:
    """I capi consigliati nella homepage Vinted, personalizzati se la sessione è autenticata.

    È il recommender di Vinted a scegliere il gusto — addestrato sul comportamento reale
    dell'utente, quindi migliore di qualsiasi vocabolario di parole chiave costruito a mano.
    Al programma resta il filtro sulle misure.
    """
    session = _get_session()
    r = session.get(BASE, timeout=25)
    if r.status_code == 403:
        session = _get_session(force=True)
        r = session.get(BASE, timeout=25)
    r.raise_for_status()

    # I payload Next.js sono stringhe JS: json.loads le de-escapa in JSON leggibile.
    pezzi = []
    for m in _PUSH_NEXT.finditer(r.text):
        try:
            pezzi.append(json.loads(m.group(1)))
        except ValueError:
            continue
    testo = "".join(pezzi)

    prodotti: list[ProdottoRisultato] = []
    visti: set[str] = set()
    for m in _ENTITY_ITEM.finditer(testo):
        raw = _oggetto_json(testo, m.end() - 1)
        if not raw:
            continue
        try:
            entity = json.loads(raw)
        except ValueError:
            continue
        p = _prodotto_da_entity(entity)
        if p is None or p.url in visti:
            continue
        visti.add(p.url)
        p.rilevanza = len(prodotti)      # l'ordine del feed è già un giudizio di Vinted
        prodotti.append(p)

    if arricchisci_descrizioni:
        # L'entity del feed porta solo la copertina: le foto col metro a nastro stanno dopo,
        # e senza tutta la sequenza il fallback vision non ha niente da leggere.
        arricchisci(prodotti, con_foto=True)

    return prodotti


# ---------------------------------------------------------------------------
# Singolo articolo dal suo indirizzo (per il parere su richiesta)
# ---------------------------------------------------------------------------

_JSON_LD = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_CONDIZIONE_SCHEMA = {
    "NewCondition": "nuovo",
    "UsedCondition": "usato",
    "RefurbishedCondition": "ricondizionato",
    "DamagedCondition": "danneggiato",
}


def articolo_da_url(url: str) -> ProdottoRisultato | None:
    """Un singolo articolo Vinted dal suo indirizzo. None se la pagina non è leggibile.

    Legge la pagina intera (~2MB): per un capo solo è accettabile, e il blocco JSON-LD
    schema.org è una fonte **stabile** — nome, descrizione completa con le misure, brand,
    prezzo, categoria e colore — al contrario delle classi CSS del DOM, che cambiano a ogni
    rilascio del sito. La taglia non è esposta nel JSON-LD: resta quella che il venditore
    scrive nella descrizione, che il consulente legge comunque.
    """
    try:
        r = _get_session().get(url, timeout=30)
        if r.status_code != 200:
            return None
    except Exception:
        return None

    dati = None
    for blocco in _JSON_LD.findall(r.text):
        try:
            candidato = json.loads(blocco)
        except ValueError:
            continue
        if isinstance(candidato, dict) and candidato.get("@type") == "Product":
            dati = candidato
            break
    if dati is None:
        return None

    offerta = dati.get("offers") or {}
    marca = dati.get("brand") or {}
    _, foto = _estrai(r.text)

    # La categoria Vinted ("Uomo Soprabiti e cappotti lunghi") classifica il capo meglio del
    # solo titolo: viene accodata al nome per chi deve dedurne il tipo.
    categoria = dati.get("category") or ""
    descrizione = dati.get("description") or ""
    if categoria:
        descrizione = f"{descrizione}\n[categoria: {categoria}]"
    if dati.get("color"):
        descrizione = f"{descrizione}\n[colore: {dati['color']}]"

    return ProdottoRisultato(
        nome=dati.get("name") or "(senza titolo)",
        brand=marca.get("name") if isinstance(marca, dict) else None,
        prezzo=_prezzo(offerta.get("price")),
        url=url,
        sito="vinted",
        immagine_url=dati.get("image"),
        condizione=_CONDIZIONE_SCHEMA.get(
            str(offerta.get("itemCondition", "")).rsplit("/", 1)[-1]
        ),
        descrizione=descrizione.strip() or None,
        foto=foto or [u for u in [dati.get("image")] if u],
    )


def categoria_da_url_html(prodotto: ProdottoRisultato) -> str:
    """Testo su cui classificare il tipo di capo: nome più categoria dichiarata da Vinted."""
    categoria = ""
    if prodotto.descrizione:
        m = re.search(r"\[categoria: ([^\]]*)\]", prodotto.descrizione)
        categoria = m.group(1) if m else ""
    return f"{prodotto.nome} {categoria}".strip()


def sessione_autenticata() -> bool:
    """True se i cookie in sessione identificano un utente loggato.

    Serve solo a dare un messaggio utile: da anonimi il feed esiste ma non è personalizzato.
    """
    try:
        r = _get_session().get(f"{BASE}/api/v2/users/current", timeout=15)
        return r.status_code == 200
    except Exception:
        return False


def scarica_immagine(url: str) -> tuple[bytes | None, str]:
    """(contenuto, media_type) di una foto Vinted, o (None, "") se non scaricabile.

    Passa dalla sessione già scaldata: le immagini stanno dietro allo stesso DataDome
    del resto del sito, quindi un fetch esterno (incluso quello dell'API Anthropic da
    URL) verrebbe bloccato.
    """
    try:
        r = _get_session().get(url, timeout=20)
        if r.status_code == 200 and r.content:
            tipo = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
            return r.content, tipo
    except Exception:
        pass
    return None, ""


# Il meta tag sta nell'<head>: si legge in streaming e si chiude la connessione appena è
# passato, invece di scaricare i ~2MB della pagina intera. Misurato: 16KB contro 1.9MB, il
# 99% di traffico in meno. Non è solo velocità — è il volume che fa scattare le difese
# anti-bot di Vinted, e una ricerca ne apre 60 di queste pagine.
_MAX_BYTE_HEAD = 96 * 1024
_MARCA_META = b'name="description"'


# Le foto a piena risoluzione stanno nella stessa zona iniziale della pagina del meta tag
# (misurato: entro ~7KB), quindi si raccolgono nella stessa lettura, senza costo aggiuntivo.
# Servono al fallback vision: il metro a nastro è quasi sempre in una foto dopo le prime due.
_FOTO_F800 = re.compile(r'https://images1\.vinted\.net/t/[^"\'\\\s]+/f800/[^"\'\\\s]+')


def _estrai(testo: str) -> tuple[str | None, list[str]]:
    """(descrizione dal meta tag, foto full-size in ordine di pubblicazione)."""
    m = _META_DESC.search(testo)
    descrizione = html_lib.unescape(m.group(1)) if m else None
    # dict.fromkeys deduplica mantenendo l'ordine: la prima foto è la copertina, le misure
    # stanno nelle successive, quindi l'ordine conta.
    foto = list(dict.fromkeys(html_lib.unescape(u) for u in _FOTO_F800.findall(testo)))
    return descrizione, foto


def _testa_in_streaming(session, url: str) -> tuple[str | None, list[str]]:
    """Legge solo l'inizio della pagina, fermandosi appena il meta tag è passato."""
    resp = session.get(url, timeout=20, stream=True)
    if resp.status_code != 200:
        resp.close()
        return None, []

    pezzi: list[bytes] = []
    letti = 0
    # Dopo aver visto il marcatore si leggono ancora due blocchi: il contenuto del tag può
    # essere lungo e finire a cavallo del blocco successivo, e le foto arrivano lì intorno.
    coda = -1
    try:
        for chunk in resp.iter_content(chunk_size=8192):
            pezzi.append(chunk)
            letti += len(chunk)
            if coda < 0 and _MARCA_META in b"".join(pezzi[-2:]):
                coda = 2
            elif coda > 0:
                coda -= 1
            if coda == 0 or letti >= _MAX_BYTE_HEAD:
                break
    finally:
        resp.close()

    return _estrai(b"".join(pezzi).decode("utf-8", "ignore"))


def _dati_da_pagina(session, url: str) -> tuple[str | None, list[str]]:
    """Descrizione del venditore (con le misure) e foto full-size, dalla pagina pubblica.

    L'endpoint API di dettaglio è protetto da DataDome (403), ma la pagina pubblica no:
    il <meta name="description"> contiene il testo del venditore, misure incluse.

    Si tenta prima in streaming (16KB invece di 2MB), con ripiego sulla lettura intera. Il
    ripiego non è teorico, serve in due casi reali:
    - su alcune pagine il meta tag compare oltre 1,8 milioni di caratteri, fuori dal cap;
    - interrompere uno stream lascia la connessione sporca, e dopo un paio di interruzioni
      curl_cffi restituisce un corpo inutilizzabile. La lettura intera la ripulisce.

    ponytail: nessun ritentativo sul 429. Provato: quando Vinted frena li frena tutti, e
    aspettare qualche secondo per capo trasformava una ricerca in dieci minuti di finto
    blocco. La difesa giusta è non farsi frenare — vedi DESCRIZIONI_PARALLELE.
    """
    try:
        descrizione, foto = _testa_in_streaming(session, url)
        if descrizione:
            return descrizione, foto
    except Exception:
        pass

    try:
        r = session.get(url, timeout=25)
        if r.status_code == 200:
            return _estrai(r.text)
    except Exception:
        pass
    return None, []


def _descrizione_da_pagina(session, url: str) -> str | None:
    """Solo la descrizione, per i chiamanti che hanno già le foto dalla lista API."""
    return _dati_da_pagina(session, url)[0]


def arricchisci(prodotti: list[ProdottoRisultato], con_foto: bool = False) -> None:
    """Riempie descrizione (e foto) leggendo una pagina per capo, DESCRIZIONI_PARALLELE insieme.

    È il costo dominante di una ricerca (una pagina da ~2MB per capo) ed è contingentata:
    Vinted risponde 429 dopo una trentina di letture ravvicinate. Va quindi chiamata sui
    capi che si vogliono davvero valutare, non su tutto il pescato — chi cerca sceglie
    prima quali candidati meritano la lettura (vedi ricerca/coordinatore).

    curl_cffi tiene un handle per thread (use_thread_local_curl=True), quindi la sessione
    già scaldata si condivide fra i thread senza ricrearla — ricrearla vorrebbe dire rifare
    il warm-up DataDome una volta per thread.
    """
    if not prodotti:
        return
    session = _get_session()

    def leggi(p: ProdottoRisultato) -> None:
        descrizione, foto = _dati_da_pagina(session, p.url)
        p.descrizione = descrizione
        if con_foto and foto:
            p.foto = foto

    with ThreadPoolExecutor(max_workers=DESCRIZIONI_PARALLELE) as pool:
        list(pool.map(leggi, prodotti))


# Token alfabetici che contano come taglia (le altre parole del profilo, es. "di vita", si
# ignorano). La lista va tenuta completa: un "xxs" mancante non faceva scattare il confronto
# fra taglie alfabetiche, e il capo passava sul solo numero in comune.
_TAGLIE_ALPHA = {"xxxs", "xxs", "xs", "s", "m", "l", "xl", "xxl", "xxxl", "xxxxl"}

# Taglia pantaloni: W = vita, L = lunghezza, in pollici ("W32 L36"). Va letta dalla stringa
# e non dai token, perché è l'adiacenza lettera-numero a dare il significato: nell'insieme
# {w, 32, l, 36} non si sa più quale numero appartenga a quale asse.
_ASSE_PANTALONI = re.compile(r"\b([wl])\s*(\d{2})\b", re.IGNORECASE)

# Le lunghezze commerciali vanno a passi di 2 pollici (L30, L32, L34, L36), quindi questa
# tolleranza vale "un passo": L34 va bene per chi porta L36, L30 no. La manopola da
# stringere a 0 se arrivano jeans troppo corti, o allargare a 4 se le liste sono vuote.
TOLLERANZA_L_POLLICI = 2

# Tetto di per_page accettato dall'API (verificato: 192 torna 96). I filtri post-fetch
# (taglia, condizione) dimezzano il raccolto, quindi si pagina finché non si raggiunge il
# numero di candidati richiesto — senza, una richiesta di 60 ne consegnava 28 in silenzio.
_PER_PAGE_MAX = 96
_MAX_PAGINE = 3


def _items_pagina(session, params: dict, pagina: int) -> list[dict]:
    """Una pagina di risultati. Su 403 ri-scalda la sessione e ritenta una volta."""
    global _session
    p = {**params, "page": pagina}
    r = session.get(ENDPOINT_ITEMS, params=p, timeout=20)
    if r.status_code == 403:
        session = _get_session(force=True)  # cookie scaduto → ri-warm e ritenta
        r = session.get(ENDPOINT_ITEMS, params=p, timeout=20)
    r.raise_for_status()
    return (r.json() or {}).get("items", [])


def assi_pantaloni(taglia: str | None) -> tuple[int | None, int | None]:
    """(vita, lunghezza) in pollici da "W32 L36". None dove l'asse non è dichiarato.

    "W32 | IT 46" → (32, None): il size_title di Vinted per i jeans uomo dichiara solo la
    vita, la lunghezza il venditore la scrive nel titolo. "L / IT 50" → (None, None): là la
    L è una taglia alfabetica, non una lunghezza, e senza cifre attaccate non viene letta.
    """
    if not taglia:
        return None, None
    assi = {m.group(1).lower(): int(m.group(2)) for m in _ASSE_PANTALONI.finditer(taglia)}
    return assi.get("w"), assi.get("l")


def taglia_piu_completa(vinted: str | None, dal_testo: str | None) -> str | None:
    """Fra la taglia dell'API Vinted e quella letta nel titolo vince chi dichiara più assi.

    Serve ai pantaloni: Vinted espone "W32 | IT 46" e la lunghezza sta solo nel titolo
    ("Levi's 501 W32 L34"). Tenendo la prima non si confronterebbe mai la L; a pari numero
    di assi vince Vinted, che è la fonte strutturata.
    """
    candidate = [t for t in (vinted, dal_testo) if t]
    if not candidate:
        return None
    return max(candidate, key=lambda t: sum(a is not None for a in assi_pantaloni(t)))


def taglia_compatibile(size_title: str | None, taglie: list[str]) -> bool:
    """Filtro morbido: True se la taglia del capo matcha una taglia utente o è assente.

    Entrambi i lati sono ridotti a token atomici ("W32 | IT 46" → w, 32, it, 46), così
    "32" matcha "W32" e il testo libero del profilo ("32 di vita 36/38") non scarta tutto.

    Quando però entrambi dichiarano una taglia alfabetica, quelle devono coincidere: il
    solo confronto numerico faceva passare "S / IT 40 / EU 36" per un utente "w32 l36",
    perché il 36 della taglia EU coincide col 36 della lunghezza gamba. Numeri nudi di
    sistemi di taglie diversi non sono confrontabili.

    Sui pantaloni gli assi W e L, quando dichiarati da entrambi, si confrontano da soli: la
    vita esatta, la lunghezza entro TOLLERANZA_L_POLLICI. Senza questo un W32 L30 passava
    per un utente W32 L36 — bastava il 32 in comune — cioè 15cm di gamba in meno.
    """
    if not size_title:
        return True
    tokens_capo = set(re.findall(r"[a-z]+|\d+", size_title.lower()))
    alpha_capo = tokens_capo & _TAGLIE_ALPHA

    tokens_utente: set[str] = set()
    for taglia in taglie:
        tokens_utente |= set(re.findall(r"[a-z]+|\d+", (taglia or "").lower()))
    alpha_utente = tokens_utente & _TAGLIE_ALPHA

    if alpha_capo and alpha_utente and not (alpha_capo & alpha_utente):
        return False

    w_capo, l_capo = assi_pantaloni(size_title)
    w_utente, l_utente = assi_pantaloni(" ".join(t or "" for t in taglie))
    if w_capo is not None and w_utente is not None and w_capo != w_utente:
        return False
    if (
        l_capo is not None and l_utente is not None
        and abs(l_capo - l_utente) > TOLLERANZA_L_POLLICI
    ):
        return False

    return any(
        (tok.isdigit() or tok in _TAGLIE_ALPHA) and tok in tokens_capo
        for tok in tokens_utente
    )


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
    # I filtri post-fetch riducono i risultati → si chiede il massimo e si pagina.
    filtri_attivi = bool(taglie or escludi_condizione_scarsa)
    params = {
        "search_text": query,
        "per_page": min(per_page, _PER_PAGE_MAX) if not filtri_attivi else _PER_PAGE_MAX,
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
    prodotti: list[ProdottoRisultato] = []
    visti: set[str] = set()

    for pagina in range(1, (_MAX_PAGINE if filtri_attivi else 1) + 1):
        items = _items_pagina(session, params, pagina)
        if not items:
            break
        for it in items:
            chiave = str(it.get("id") or it.get("url"))
            if chiave in visti:          # le pagine Vinted si sovrappongono un po'
                continue
            visti.add(chiave)
            p = _mappa_item(it)
            if taglie and not taglia_compatibile(p.taglia_disponibile, taglie):
                continue
            if escludi_condizione_scarsa and (p.condizione or "").lower() == "soddisfacente":
                continue
            # Posizione nell'ordine di rilevanza Vinted, prima di qualsiasi riordino nostro:
            # è il miglior spareggio disponibile quando il fit non discrimina (vedi fit.py).
            p.rilevanza = len(prodotti)
            prodotti.append(p)
        if len(prodotti) >= per_page:
            break

    prodotti = prodotti[:per_page]  # tronca PRIMA dell'arricchimento: max per_page GET

    # ponytail: descrizione (per il fit) presa dal meta della pagina pubblica, una GET per
    # prodotto. Ceiling: N pagine HTML, bounded a per_page; se pesa, ridurre ai top-3.
    if arricchisci_descrizioni:
        arricchisci(prodotti)

    return prodotti


if __name__ == "__main__":
    # Self-check: richiede rete e un IP non bloccato da DataDome.
    assert taglia_compatibile("M / IT 48 / EU 44", ["M"])
    assert taglia_compatibile("IT 42", ["42"])
    assert taglia_compatibile(None, ["M"])
    assert not taglia_compatibile("12 anni / 152 cm", ["M", "32", "42"])
    assert taglia_compatibile("W32 | IT 46", ["32 di vita 36/38 di lunghezza"])
    assert not taglia_compatibile("W28 | IT 42", ["32 di vita 36/38 di lunghezza"])
    assert not taglia_compatibile("Taglia unica", ["l", "32 di vita 36/38 di lunghezza", "47"])
    # Taglie alfabetiche in conflitto: il numero in comune non basta più a farle passare
    # (casi reali dal feed Vinted, dove il 36 era la taglia EU donna e non la gamba)
    assert not taglia_compatibile("S / IT 40 / EU 36", ["w32 l36"])
    assert not taglia_compatibile("XXS / IT 36 / EU 32", ["w32 l36"])
    assert not taglia_compatibile("M / IT 48 / EU 44", ["l, xl"])
    assert taglia_compatibile("L / IT 50", ["l, xl"])
    assert taglia_compatibile("XL", ["l, xl"])
    # Se solo un lato dichiara l'alfabetica, il confronto numerico resta valido
    assert taglia_compatibile("W32 | IT 46", ["w32 l36"])

    # Assi dei pantaloni: la lunghezza va letta dalla stringa, non dai token
    assert assi_pantaloni("W32 L36") == (32, 36)
    assert assi_pantaloni("W32 | IT 46") == (32, None)   # Vinted non espone la lunghezza
    assert assi_pantaloni("L / IT 50") == (None, None)   # qui la L è una taglia, non una gamba
    assert assi_pantaloni(None) == (None, None)

    # La L dichiarata nel titolo va confrontata: prima bastava il 32 in comune e un W32 L30
    # passava per chi porta W32 L36, cioè 15cm di gamba in meno.
    assert taglia_compatibile("W32 L36", ["w32 l36"])
    assert taglia_compatibile("W32 L34", ["w32 l36"])         # un passo di lunghezza: passa
    assert not taglia_compatibile("W32 L30", ["w32 l36"])     # tre passi: fuori
    assert not taglia_compatibile("W34 L36", ["w32 l36"])     # la vita deve coincidere
    # Con la lunghezza non dichiarata da un lato il filtro resta morbido, come prima
    assert taglia_compatibile("W32 L30", ["32"])

    # Fra le due taglie vince chi dichiara più assi; a pari assi vince quella di Vinted
    assert taglia_piu_completa("W32 | IT 46", "W32 L34") == "W32 L34"
    assert taglia_piu_completa("W32 | IT 46", "L") == "W32 | IT 46"
    assert taglia_piu_completa(None, "W32 L34") == "W32 L34"
    assert taglia_piu_completa("L / IT 50", None) == "L / IT 50"
    assert taglia_piu_completa(None, None) is None

    risultati = cerca_vinted("nike", budget=40, arricchisci_descrizioni=False)
    for p in risultati[:5]:
        print(f"- {p.nome} | {p.brand} | €{p.prezzo} | {p.taglia_disponibile} | {p.condizione}")
    assert len(risultati) > 0, "Nessun risultato: possibile blocco DataDome o endpoint cambiato"
    assert all(p.prezzo is None or p.prezzo <= 40 for p in risultati), "price_to non rispettato"

    # Le descrizioni sono la base del confronto sulle misure. L'invariante da proteggere non
    # è "quasi tutti i capi ne hanno una" (diversi venditori non scrivono niente), ma che la
    # lettura in streaming non perda nulla di ciò che la pagina intera contiene: è per
    # questo che _descrizione_da_pagina ripiega sulla lettura completa.
    sessione = _get_session()
    campione = cerca_vinted("t-shirt misure spalle lunghezza", budget=60, per_page=6,
                            catalog_ids="5", arricchisci_descrizioni=False)
    perse = 0
    for prod in campione:
        completa = sessione.get(prod.url, timeout=25)
        atteso = _META_DESC.search(completa.text) if completa.status_code == 200 else None
        ottenuto = _descrizione_da_pagina(sessione, prod.url)
        if atteso and atteso.group(1).strip() and not ottenuto:
            perse += 1
    print(f"descrizioni perse dallo streaming: {perse}/{len(campione)}")
    assert perse == 0, "lo streaming perde descrizioni che la pagina intera contiene"

    con_taglia = cerca_vinted("felpa nike", budget=40, arricchisci_descrizioni=False, taglie=["M"])
    for p in con_taglia[:5]:
        print(f"- [taglia M] {p.nome} | {p.taglia_disponibile}")
    assert all(taglia_compatibile(p.taglia_disponibile, ["M"]) for p in con_taglia)
    print(f"\nOK: {len(risultati)} prodotti, {len(con_taglia)} con filtro taglia M")
