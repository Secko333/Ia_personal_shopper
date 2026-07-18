"""Task template per ricerche su Zalando."""

from __future__ import annotations

from Ia_personal_shopper.config import MAX_RISULTATI_PER_SITO
from Ia_personal_shopper.models import ProfiloUtente


def build_task(query: str, budget: float | None, profilo: ProfiloUtente, genere: str | None = None) -> str:
    taglia_info = ""
    if profilo.taglie.top:
        taglia_info = f" (cerca preferibilmente taglia {profilo.taglie.top})"

    filtro_budget = (
        f"Filtra o ignora prodotti con prezzo superiore a {budget:.0f}€. "
        if budget
        else ""
    )
    filtro_genere = (
        f"Filtra o cerca SOLO nella sezione '{genere}': ignora i prodotti dell'altro sesso. "
        if genere
        else ""
    )

    return (
        f"Vai su https://www.zalando.it. "
        f"Usa la barra di ricerca principale per cercare: '{query}'{taglia_info}. "
        f"Aspetta che la pagina dei risultati si carichi completamente. "
        f"Scorri verso il basso due volte per caricare più prodotti (lazy load). "
        f"{filtro_budget}"
        f"{filtro_genere}"
        f"Raccogli i primi {MAX_RISULTATI_PER_SITO} prodotti visibili. "
        f"Per ogni prodotto estrai: nome completo, brand, prezzo numerico in euro, "
        f"URL della pagina prodotto, URL dell'immagine principale. "
        f"Il campo 'sito' deve essere sempre 'zalando'. "
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
