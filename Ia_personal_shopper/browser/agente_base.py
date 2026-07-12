"""Factory per creare agenti browser-use configurati per ogni sito."""

from __future__ import annotations

from pathlib import Path

from browser_use import Agent
from browser_use.browser.profile import BrowserProfile
from langchain_anthropic import ChatAnthropic

from Ia_personal_shopper.config import (
    BROWSER_DATA_DIR,
    DOMINI_SITI,
    MODELLO_BROWSER,
)
from Ia_personal_shopper.models import RisultatiRicerca


def _crea_profilo_browser(sito: str) -> BrowserProfile:
    """Crea un BrowserProfile con user_data_dir dedicato per sito (cookie persistenti)."""
    cartella_sito: Path = BROWSER_DATA_DIR / sito
    cartella_sito.mkdir(parents=True, exist_ok=True)

    dominio = DOMINI_SITI.get(sito)
    domini_permessi = [f"*.{dominio}", dominio] if dominio else None

    return BrowserProfile(
        user_data_dir=cartella_sito,
        headless=False,                       # headful = meno ban, user può intervenire su CAPTCHA
        enable_default_extensions=True,       # uBlock + cookie banner handler inclusi
        allowed_domains=domini_permessi,      # il browser naviga solo sul sito target
    )


def crea_agente_ricerca(sito: str, task: str) -> Agent:
    """Crea un Agent browser-use configurato per cercare prodotti su un sito."""
    llm = ChatAnthropic(model_name=MODELLO_BROWSER, temperature=0.0)
    profilo = _crea_profilo_browser(sito)

    return Agent(
        task=task,
        llm=llm,
        browser_profile=profilo,
        output_model_schema=RisultatiRicerca,
        max_failures=3,
        use_vision=True,
        use_thinking=True,
        enable_planning=True,
    )


def crea_agente_carrello(url_prodotto: str, task: str) -> Agent:
    """Crea un Agent dedicato all'aggiunta al carrello di un prodotto specifico."""
    llm = ChatAnthropic(model_name=MODELLO_BROWSER, temperature=0.0)

    # Rileva il sito dall'URL per usare il profilo browser corretto
    sito = "generico"
    for nome_sito, dominio in DOMINI_SITI.items():
        if dominio in url_prodotto:
            sito = nome_sito
            break

    profilo = _crea_profilo_browser(sito)
    # Per il carrello non limitiamo i domini (potrebbe dover caricare risorse esterne)
    profilo.allowed_domains = None

    return Agent(
        task=task,
        llm=llm,
        browser_profile=profilo,
        max_failures=3,
        use_vision=True,
        use_thinking=True,
    )
