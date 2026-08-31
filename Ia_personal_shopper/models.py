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
    # Letti dal titolo/descrizione insieme alle misure (una sola chiamata, vedi
    # valutazione/fit.estrai_da_descrizioni): None = il venditore non li ha scritti.
    colore: str | None = None            # "nero", "verde militare"
    fit_dichiarato: str | None = None    # "oversize", "slim", "vestibilità ampia"
    descrizione: str | None = None       # testo venditore (Vinted): spesso contiene le misure del capo
    foto: list[str] = Field(default_factory=list)   # tutte le foto (Vinted le dà gratis nella lista API):
                                                    # le misure col metro stanno spesso dalla terza in poi
    rilevanza: int = 0                   # posizione nell'ordine di rilevanza del sito, 0 = primo


class RisultatiRicerca(BaseModel):
    """Schema restituito dall'agente browser tramite output_model_schema."""
    prodotti: list[ProdottoRisultato] = Field(default_factory=list)


class ParametriRicerca(BaseModel):
    """Parametri strutturati estratti dalla richiesta in linguaggio naturale (vedi ricerca/interprete.py)."""
    query: str
    tipo_capo: str = "altro"                          # "top" | "pantaloni" | "scarpe" | "altro"
    colori: list[str] = Field(default_factory=list)   # nomi colore italiani mappabili su color_ids Vinted
    genere: str | None = None                         # "uomo" | "donna" | None
    # Due assi indipendenti, non un enum unico: così "oversize croppata" resta esprimibile.
    # None dall'LLM = la richiesta non lo dice → si ricade sul profilo (vedi interpreta_ricerca),
    # dopo il quale sono sempre valorizzati.
    vestibilita: str | None = None                     # "aderente" | "regular" | "oversize" → larghezze
    lunghezza: str | None = None                       # "corta" | "regular" | "lunga" → lunghezza
    # Termini di stile dell'utente, UNO PER RICERCA SEPARATA (max 3). Non si sommano nella
    # stessa query: la ricerca Vinted è un'intersezione, e ogni parola di gusto in più fa
    # crollare la quota di capi che dichiarano le misure (misurato: 65% → 5%). Query distinte
    # invece coprono più gusto senza pagare quel prezzo (vedi ricerca/coordinatore._cerca_vinted).
    termini_stile: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Valutazione editoriale di ogni prodotto
# ---------------------------------------------------------------------------

class ValutazioneProdotto(BaseModel):
    indice: int                      # posizione 1..N del prodotto nella lista valutata
    si_adatta_fisico: bool
    ottimo_affare: bool
    commento: str                    # max ~15 parole, diretto e onesto
    raccomandazione: str             # "compra" | "considera" | "evita"


class ValutazioniRicerca(BaseModel):
    valutazioni: list[ValutazioneProdotto] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Misure: target derivate dal profilo vs misure reali del capo (vedi valutazione/fit.py)
# ---------------------------------------------------------------------------

class MisureCapo(BaseModel):
    """Misure del singolo capo, come dichiarate dal venditore. Le larghezze sono già
    normalizzate a misura piatta (ascella-ascella), non circonferenza."""
    spalle_cm: float | None = None
    petto_flat_cm: float | None = None
    lunghezza_cm: float | None = None
    vita_flat_cm: float | None = None
    lunghezza_interna_cm: float | None = None
    fonte: str | None = None         # "descrizione" | "foto" | None


class MisureTarget(BaseModel):
    """Misure che il capo dovrebbe avere per vestire come richiesto, derivate dal profilo."""
    spalle_cm: float | None = None
    petto_flat_cm: float | None = None
    lunghezza_cm: float | None = None
    vita_flat_cm: float | None = None
    lunghezza_interna_cm: float | None = None


class EsitoFit(BaseModel):
    # Classe esplicita invece di dedurla da una media pesata: con la media, un capo con il
    # petto sbagliato di 9cm risultava "su misura" perché le altre due misure compensavano.
    classe: int = 2                  # 0 = su misura · 1 = misure parziali · 2 = non dichiarate
    punteggio: float                 # 0..1, precisione sulle misure dichiarate (solo spareggio)
    confidenza: float                # 0..1, quota di peso coperta da misure note (0 = nessuna misura)
    scartato: bool = False
    motivo_scarto: str | None = None  # "lunghezza 78 vs 64 (+14)"
    scarto_max_cm: float = 0.0       # scarto peggiore su una misura prioritaria, in cm
    dettaglio: str = ""              # "spalle 52 ✓ · lungh 66 ✓ · petto n/d"


class ParereCapo(BaseModel):
    """Parere argomentato su un singolo capo (vedi valutazione/parere.py)."""
    verdetto: str                    # "compra" | "considera" | "evita"
    sintesi: str
    a_favore: list[str] = Field(default_factory=list)
    contro: list[str] = Field(default_factory=list)
    da_chiedere: list[str] = Field(default_factory=list)   # cosa domandare al venditore


class ReportFit(BaseModel):
    """Contabilità della selezione per misure, da stampare in CLI."""
    attivo: bool = False             # False = niente misure target utili per questo tipo di capo
    target: MisureTarget = Field(default_factory=MisureTarget)
    candidati: int = 0
    scartati: int = 0
    miglior_scartato: str | None = None   # "spalle 47 (−5)": segnala quando conviene allargare la soglia
    fuori_taglia: int = 0             # taglia dichiarata (anche nel titolo) incompatibile
    in_coda: int = 0                  # senza misure dichiarate: mostrati solo su richiesta
    # Senza segnalarlo, una chiave API rotta o un rate limit è indistinguibile da "nessun
    # venditore ha scritto le misure".
    errore_descrizioni: bool = False   # la chiamata sulle descrizioni è fallita (tutti i capi)


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
    versione: int = 2                        # 2 = stili/colori/occasioni separati (vedi gestore._migra_v2)
    nome: str = "Utente"
    fisico: FisicoUtente = Field(default_factory=FisicoUtente)
    taglie: TaglieUtente = Field(default_factory=TaglieUtente)
    genere: str | None = None                # "uomo" | "donna" | None (nessun filtro)
    # Campi separati perché finiscono in posti diversi: gli stili entrano nelle query di
    # ricerca (è il vocabolario che decide COSA viene pescato), i colori e le occasioni
    # servono solo al giudizio del consulente.
    preferenze_stile: list[str] = Field(default_factory=list)   # "grunge", "modern western", ...
    colori_preferiti: list[str] = Field(default_factory=list)   # "nero", "verde", ...
    occasioni: list[str] = Field(default_factory=list)          # "serate", "tempo libero", ...
    vestibilita_preferita: str | None = None  # "aderente" | "regular" | "oversize": default
                                              # quando la richiesta non dice come deve vestire
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
# Guardaroba (capi posseduti) e proposte proattive
# ---------------------------------------------------------------------------

class CapoGuardaroba(BaseModel):
    id: str
    aggiunto_il: str
    descrizione: str            # "giacca di jeans blu Levi's" — testo libero, letto dall'LLM


class ListaGuardaroba(BaseModel):
    versione: int = 1
    capi: list[CapoGuardaroba] = Field(default_factory=list)


class Proposta(BaseModel):
    titolo: str                 # "Blazer beige per i tuoi chino scuri"
    motivo: str                 # perché: completa il guardaroba / valorizza il fisico / gusto
    ricerche: list[str] = Field(default_factory=list)   # 1 query = capo singolo; 2-3 = outfit


class ProposteRicerca(BaseModel):
    """Wrapper per il parse JSON delle proposte (vedi ricerca/proposte.py)."""
    proposte: list[Proposta] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Prodotto arricchito (risultato + valutazione) per il display
# ---------------------------------------------------------------------------

class ProdottoArricchito(BaseModel):
    prodotto: ProdottoRisultato
    valutazione: ValutazioneProdotto | None = None
    misure: MisureCapo | None = None     # misure dichiarate dal venditore, se trovate
    fit: EsitoFit | None = None          # confronto con le target (None = fit non valutabile)
    affinita_gusto: int = 0              # termini di stile dell'utente presenti nel titolo:
                                         # rompe i pari dentro la stessa fascia di fit
