"""Task template browser per Vinted (solo preferiti: la ricerca usa l'API JSON, vedi vinted_api.py).

Contiene anche l'apertura della finestra di login: serve al feed personalizzato, che senza
sessione autenticata mostra capi generici invece dei consigli dell'utente.
"""

from __future__ import annotations

from Ia_personal_shopper.config import BROWSER_DATA_DIR


# Il Chromium di Playwright viene riconosciuto come automatizzato e DataDome blocca la
# sessione ("Abbiamo notato attività insolite"). Queste opzioni togliono i segnali più
# evidenti: si usa il Chrome vero installato sul sistema invece del Chromium incluso, e si
# rimuovono i flag di automazione che il browser altrimenti espone a navigator.webdriver.
_ARGS_ANTI_RILEVAMENTO = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
]
_DEFAULT_DA_IGNORARE = ["--enable-automation", "--disable-extensions"]


def apri_login(attesa=input) -> bool:
    """Apre una finestra browser su Vinted e attende che l'utente acceda da sé.

    Nessuna credenziale viene chiesta, letta o salvata dal programma: l'utente accede
    a mano nella finestra, e il browser scrive i cookie di sessione nel profilo persistente
    su disco. Da lì vinted_api li rilegge per autenticare le richieste API.

    `attesa` è iniettabile per i test; per default blocca sul terminale.
    Ritorna False se Playwright non è disponibile o nessun browser si apre.
    """
    profilo = BROWSER_DATA_DIR / "vinted"
    profilo.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False

    try:
        with sync_playwright() as p:
            contesto = None
            # Prima il Chrome di sistema: ha un fingerprint reale. Il Chromium incluso è
            # l'ultima risorsa, ed è quello che DataDome blocca più facilmente.
            for canale in ("chrome", "msedge", None):
                try:
                    contesto = p.chromium.launch_persistent_context(
                        str(profilo),
                        headless=False,
                        channel=canale,
                        args=_ARGS_ANTI_RILEVAMENTO,
                        ignore_default_args=_DEFAULT_DA_IGNORARE,
                        viewport=None,          # finestra reale, non 800x600 da automazione
                    )
                    break
                except Exception:
                    continue
            if contesto is None:
                return False

            pagina = contesto.pages[0] if contesto.pages else contesto.new_page()
            pagina.goto("https://www.vinted.it/", wait_until="domcontentloaded")
            attesa(
                "\n  Accedi a Vinted nella finestra appena aperta, poi torna qui "
                "e premi Invio…\n"
            )
            try:
                contesto.close()
            except Exception:
                pass
        return True
    except Exception:
        return False


def build_task_preferiti_vinted(url_prodotto: str) -> str:
    """Su Vinted aggiungiamo ai preferiti invece di fare offerte (che sono irreversibili)."""
    return (
        f"Vai su {url_prodotto}. "
        f"Aggiungi l'articolo ai tuoi preferiti/wishlist cliccando sul cuore o l'icona preferiti. "
        f"NON fare offerte e NON procedere all'acquisto. "
        f"Fermati dopo aver aggiunto ai preferiti. "
        f"Conferma il successo dell'operazione."
    )
