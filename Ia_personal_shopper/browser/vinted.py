"""Task template browser per Vinted (solo preferiti: la ricerca usa l'API JSON, vedi vinted_api.py)."""

from __future__ import annotations


def build_task_preferiti_vinted(url_prodotto: str) -> str:
    """Su Vinted aggiungiamo ai preferiti invece di fare offerte (che sono irreversibili)."""
    return (
        f"Vai su {url_prodotto}. "
        f"Aggiungi l'articolo ai tuoi preferiti/wishlist cliccando sul cuore o l'icona preferiti. "
        f"NON fare offerte e NON procedere all'acquisto. "
        f"Fermati dopo aver aggiunto ai preferiti. "
        f"Conferma il successo dell'operazione."
    )
