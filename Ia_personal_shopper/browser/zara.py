"""Task template per ricerche su Zara."""

from __future__ import annotations

from Ia_personal_shopper.config import MAX_RISULTATI_PER_SITO
from Ia_personal_shopper.models import ProfiloUtente


def build_task(query: str, budget: float | None, profilo: ProfiloUtente, genere: str | None = None) -> str:
    taglia_info = ""
    if profilo.taglie.top:
        taglia_info = f" (cerca preferibilmente taglia {profilo.taglie.top})"

    # Zara non ha un filtro prezzo affidabile via UI — il budget viene applicato
    # in post-aggregazione in aggregatore.py, quindi non lo includiamo nel task.
    nota_budget = (
        f"Nota: successivamente filtreremo per prezzo massimo {budget:.0f}€. "
        if budget
        else ""
    )
    filtro_genere = (
        f"Filtra o cerca SOLO nella sezione '{genere}': ignora i prodotti dell'altro sesso. "
        if genere
        else ""
    )

    return (
        f"Vai su https://www.zara.com/it. "
        f"Clicca sull'icona di ricerca (lente d'ingrandimento) in alto e cerca: '{query}'{taglia_info}. "
        f"Attendi il caricamento completo dei risultati (la pagina usa React, aspetta almeno 3 secondi). "
        f"Scorri verso il basso due volte per caricare più prodotti. "
        f"{nota_budget}"
        f"{filtro_genere}"
        f"Raccogli i primi {MAX_RISULTATI_PER_SITO} prodotti visibili. "
        f"Per ogni prodotto estrai: nome completo, brand (di solito 'Zara'), prezzo numerico in euro, "
        f"URL della pagina prodotto, URL dell'immagine principale. "
        f"Il campo 'sito' deve essere sempre 'zara'. "
        f"Restituisci i dati nel formato strutturato richiesto."
    )


def build_task_carrello(url_prodotto: str, taglia: str | None) -> str:
    selezione_taglia = (
        f"Seleziona la taglia {taglia} se disponibile. "
        if taglia
        else "Seleziona la taglia più adatta se richiesta. "
    )
    return (
        f"Vai su {url_prodotto}. "
        f"{selezione_taglia}"
        f"Clicca su 'Aggiungi al carrello' o pulsante equivalente. "
        f"Fermati PRIMA di qualsiasi pagina di pagamento o checkout. "
        f"Conferma che l'articolo è stato aggiunto al carrello con successo."
    )
