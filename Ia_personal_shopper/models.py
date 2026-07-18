"""Modelli dati condivisi dell'applicazione."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Output strutturato degli agenti browser
# ---------------------------------------------------------------------------

class ProdottoRisultato(BaseModel):
    nome: str
    brand: str | None = None
    prezzo: float | None = None          # None = non rilevato
    taglia_disponibile: str | None = None
    url: str
    sito: str                            # "zalando" | "zara" | "vinted"
    immagine_url: str | None = None
    condizione: str | None = None        # Vinted: "ottimo stato", "buone condizioni", ecc.
    descrizione: str | None = None       # testo venditore (Vinted): spesso contiene le misure del capo


class RisultatiRicerca(BaseModel):
    """Schema restituito dall'agente browser tramite output_model_schema."""
    prodotti: list[ProdottoRisultato] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Valutazione editoriale di ogni prodotto
# ---------------------------------------------------------------------------

class ValutazioneProdotto(BaseModel):
    indice: int                      # posizione 1..N del prodotto nella lista valutata
    si_adatta_fisico: bool
    ottimo_affare: bool
    commento: str                    # max ~15 parole, diretto e onesto
    raccomandazione: str             # "compra" | "considera" | "evita"
    vestibilita: str | None = None   # es. "spalle 46cm vs tue 48 → stretto" (se il capo riporta misure)


class ValutazioniRicerca(BaseModel):
    valutazioni: list[ValutazioneProdotto] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Profilo fisico e preferenze utente
# ---------------------------------------------------------------------------

class FisicoUtente(BaseModel):
    altezza_cm: int | None = None
    peso_kg: float | None = None
    larghezza_spalle_cm: int | None = None   # chiave per il fit dei top vs pit-to-pit del capo
    circonferenza_petto_cm: int | None = None
    circonferenza_vita_cm: int | None = None
    circonferenza_fianchi_cm: int | None = None
    circonferenza_collo_cm: int | None = None
    lunghezza_gamba_interna_cm: int | None = None
    note_corporatura: str | None = None


class TaglieUtente(BaseModel):
    top: str | None = None
    pantaloni: str | None = None
    scarpe: str | None = None


class ProfiloUtente(BaseModel):
    versione: int = 1
    nome: str = "Utente"
    fisico: FisicoUtente = Field(default_factory=FisicoUtente)
    taglie: TaglieUtente = Field(default_factory=TaglieUtente)
    genere: str | None = None                # "uomo" | "donna" | None (nessun filtro)
    preferenze_stile: list[str] = Field(default_factory=list)
    gusti_positivi: list[str] = Field(default_factory=list)   # appreso da feedback/foto: cosa piace
    gusti_negativi: list[str] = Field(default_factory=list)   # appreso da feedback: cosa evitare
    brand_esclusi: list[str] = Field(default_factory=list)
    budget_default: float = 100.0
    siti_attivi: list[str] = Field(default_factory=lambda: ["zalando", "zara", "vinted"])
    aggiornato_il: str = ""


# ---------------------------------------------------------------------------
# Preferiti
# ---------------------------------------------------------------------------

class ArticoloPreferito(BaseModel):
    id: str
    salvato_il: str
    query_originale: str
    prodotto: ProdottoRisultato


class ListaPreferiti(BaseModel):
    versione: int = 1
    preferiti: list[ArticoloPreferito] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Prodotto arricchito (risultato + valutazione) per il display
# ---------------------------------------------------------------------------

class ProdottoArricchito(BaseModel):
    prodotto: ProdottoRisultato
    valutazione: ValutazioneProdotto | None = None
