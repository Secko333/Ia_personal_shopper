"""Apprendimento dei gusti da un articolo segnalato con /mipiace o /nonmipiace.

Il brand da solo non è un gusto: "Vintage Dressing" è un negozio, non uno stile, e non
serve a costruire una ricerca. Qui si ricava dall'articolo un descrittore breve e
riusabile come termine di ricerca su Vinted, che l'interprete userà per generare le
varianti di gusto delle ricerche successive (vedi ricerca/interprete.py).
"""

from __future__ import annotations

import json
import re

import anthropic

from Ia_personal_shopper.config import MODELLO_VALUTAZIONE
from Ia_personal_shopper.models import ProdottoRisultato, ProfiloUtente

_PROMPT = """Da questo articolo di abbigliamento ricava un descrittore di STILE, breve e
riusabile come termine di ricerca su Vinted.

Regole:
- da 3 a 6 parole, in italiano o coi termini inglesi che i venditori usano davvero
- descrivi stile, stampa, epoca, colore: "band tee nera anni 90 stampa rock"
- NON nominare il brand né il negozio: serve lo stile, non l'etichetta
- NON usare aggettivi di giudizio ("bello", "elegante") né la taglia o il prezzo
- rispondi con il solo descrittore, senza virgolette e senza spiegazioni

Articolo: {nome}
Categoria/brand: {brand}
Descrizione del venditore: {descrizione}"""


async def descrittore_stile(prodotto: ProdottoRisultato) -> str | None:
    """Descrittore di stile dell'articolo, o None se la chiamata non riesce."""
    try:
        client = anthropic.AsyncAnthropic()
        resp = await client.messages.create(
            model=MODELLO_VALUTAZIONE,
            max_tokens=60,
            messages=[{
                "role": "user",
                "content": _PROMPT.format(
                    nome=prodotto.nome,
                    brand=prodotto.brand or "non indicato",
                    descrizione=(prodotto.descrizione or "non disponibile")[:400],
                ),
            }],
        )
        testo = resp.content[0].text.strip().strip('"').strip()
    except Exception:
        return None

    # Una risposta lunga significa che il modello ha spiegato invece di descrivere:
    # meglio nessun gusto appreso che una frase intera dentro le query di ricerca.
    if not testo or len(testo.split()) > 10:
        return None
    return testo


# ---------------------------------------------------------------------------
# Affinità di gusto: quanto il titolo di un capo somiglia al vocabolario dell'utente
# ---------------------------------------------------------------------------

# Termini d'epoca: su Vinted li scrive metà dei venditori, quindi non discriminano nulla —
# né come termine di ricerca (vedi ricerca/interprete.py, che li importa da qui) né come
# affinità di gusto, da qualunque fonte arrivino. Anche un descrittore appreso da /mipiace
# tipo "band tee nera vintage" non deve far valere il suo "vintage".
EPOCHE_GENERICHE = {"vintage", "90s", "80s", "70s", "2000s", "y2k", "retro", "anni"}

# Parole presenti in quasi ogni titolo: non distinguono niente e falserebbero il conteggio.
_TROPPO_GENERICHE = {
    "shirt", "tshirt", "t-shirt", "maglietta", "maglia", "magliette", "top", "capo",
    "uomo", "donna", "unisex", "manica", "maniche", "corta", "corte", "lunga", "lunghe",
    "misure", "misura", "spalle", "lunghezza", "vita", "petto", "torace", "taglia",
    "jeans", "pantaloni", "pantalone", "camicia", "felpa", "scarpe", "polo", "bermuda",
    "cotone", "nuovo", "nuova", "usato", "originale", "vera", "vero",
} | EPOCHE_GENERICHE


_PROMPT_FEED = """Questi sono i capi che il sistema di raccomandazione di Vinted consiglia a
un utente, quindi riflettono i suoi gusti reali osservati dal suo comportamento.

Ricava da 3 a 6 termini di STILE ricorrenti, utilizzabili come termini di ricerca su Vinted.

Regole:
- ogni termine da 1 a 3 parole: sottoculture, scene, epoche con stile, dettagli
  costruttivi, o brand che definiscono uno stile ("carhartt", "single stitch", "band tee")
- solo ciò che RICORRE: ignora il capo isolato che non c'entra col resto
- NON termini generici che valgono per qualunque capo ("cotone", "maglietta", "uomo",
  "nuovo", nomi di colore da soli)
- se i capi non hanno nessuno stile comune riconoscibile, restituisci una lista vuota

Rispondi SOLO con JSON: {{"stili": ["...", "..."]}}

CAPI CONSIGLIATI:
{titoli}"""


async def impara_da_feed(prodotti: list[ProdottoRisultato]) -> list[str]:
    """Termini di stile ricorrenti nei capi consigliati dal feed Vinted.

    Il recommender di Vinted è addestrato sul comportamento reale dell'utente, quindi come
    fonte di gusto batte qualunque vocabolario scritto a mano. Da qui i termini entrano nel
    vocabolario di affinità e nella scelta del termine di stile delle ricerche.

    ATTENZIONE al chiamante: da sessione anonima il feed NON è personalizzato (mostra
    Barbie e gonne leopardate), e impararci sopra avvelenerebbe il profilo. Chiamare solo
    con sessione autenticata.
    """
    titoli = [f"- {p.nome}" for p in prodotti if p.nome]
    if not titoli:
        return []

    try:
        client = anthropic.AsyncAnthropic()
        resp = await client.messages.create(
            model=MODELLO_VALUTAZIONE,
            max_tokens=300,
            messages=[{"role": "user", "content": _PROMPT_FEED.format(titoli="\n".join(titoli))}],
        )
        raw = resp.content[0].text
        dati = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
    except Exception:
        return []

    stili = []
    for voce in dati.get("stili") or []:
        voce = str(voce).strip()
        # Un termine lunghissimo è una frase, non uno stile: nelle query farebbe danni.
        if voce and len(voce.split()) <= 3:
            stili.append(voce)
    return stili[:6]


def _nomi_colore() -> frozenset[str]:
    """Nomi di colore in ogni concordanza: un colore non è un gusto.

    Un descrittore appreso da /mipiace ("band tee NERA stampa rock") porterebbe dentro il
    colore, e allora qualunque capo nero prenderebbe un punto di affinità — anche un Hugo
    Boss. I colori dell'utente vivono già in `colori_preferiti`, dove servono al giudizio.

    Import a livello di funzione: interprete importa questo modulo, quindi l'inverso a
    import time sarebbe un ciclo.
    """
    from Ia_personal_shopper.ricerca.interprete import COLOR_IDS, forme_colore

    return frozenset(f for nome in COLOR_IDS for f in forme_colore(nome))


def vocabolari_gusto(profilo: ProfiloUtente) -> tuple[set[str], set[str]]:
    """(termini di gusto, termini da evitare) estratti dal solo profilo dell'utente.

    Deliberatamente NON include le varianti di ricerca generate per la query corrente: le
    varianti servono a pescare, il vocabolario a giudicare. Includerle premiava i termini
    generici che contengono — con "t-shirt vintage" tra le varianti, "Ralph Lauren 90s
    Vintage" prendeva un punto di affinità come una band tee grunge.
    """
    escluse = _TROPPO_GENERICHE | _nomi_colore()

    def termini(voci: list[str]) -> set[str]:
        trovati = set()
        for voce in voci:
            for token in re.findall(r"[\w'-]+", (voce or "").lower()):
                if len(token) > 2 and token not in escluse:
                    trovati.add(token)
        return trovati

    positivi = termini([*profilo.preferenze_stile, *profilo.gusti_positivi])
    negativi = termini(profilo.gusti_negativi)
    return positivi - negativi, negativi


def affinita_gusto(
    prodotto: ProdottoRisultato, positivi: set[str], negativi: set[str]
) -> int:
    """Quanti termini di gusto compaiono nel titolo/brand, meno il doppio di quelli da evitare.

    La ricerca Vinted è fuzzy: anche cercando "band tee alt rock single stitch" tornano
    Shein e Maison Margiela. Questo punteggio li fa scendere sotto i capi che nel titolo
    dicono davvero "vintage single stitch anni 90".
    """
    testo = f"{prodotto.nome} {prodotto.brand or ''}".lower()
    a_favore = sum(1 for t in positivi if t in testo)
    contro = sum(1 for t in negativi if t in testo)
    return a_favore - 2 * contro


if __name__ == "__main__":
    # Self-check offline; descrittore_stile richiede rete e chiave API.
    p = ProfiloUtente(
        preferenze_stile=["Grunge", "Alt-Rock", "Modern Western", "Indie", "Slim Rock"],
        gusti_positivi=["band tee nera vintage, stampa grafica rock"],
        gusti_negativi=["pantaloni caviglia", "Shein"],
    )
    pos, neg = vocabolari_gusto(p)

    # Il vocabolario tiene i termini distintivi e butta quelli onnipresenti
    assert "grunge" in pos and "western" in pos and "rock" in pos and "indie" in pos
    assert "shirt" not in pos and "maniche" not in pos and "misure" not in pos
    assert "shein" in neg and "caviglia" in neg
    # Un termine da evitare non può stare anche tra i positivi
    assert "shein" not in pos
    # "vintage" non è nel profilo: non deve entrare da nessun'altra parte, o premierebbe
    # mezzo catalogo Vinted (Ralph Lauren 90s Vintage quanto una band tee grunge)
    assert "vintage" not in pos

    # Un descrittore appreso porta con sé epoca e colore: nessuno dei due è un gusto
    imparato = ProfiloUtente(
        preferenze_stile=["Grunge"],
        gusti_positivi=["band tee nera stampa rock anni 2000"],
    )
    pos_imp, _ = vocabolari_gusto(imparato)
    assert {"band", "tee", "stampa", "rock", "grunge"} <= pos_imp, pos_imp
    assert "nera" not in pos_imp and "vintage" not in pos_imp and "anni" not in pos_imp
    # Un Hugo Boss nero non deve guadagnare affinità dal solo colore
    nero_mainstream = ProdottoRisultato(
        nome="T-shirt uomo Hugo Boss nera", url="u", sito="vinted", brand="Hugo Boss"
    )
    assert affinita_gusto(nero_mainstream, pos_imp, set()) == 0

    def _p(nome, brand=None):
        return ProdottoRisultato(nome=nome, url="u", sito="vinted", brand=brand)

    buono = _p("Nirvana T-Shirt Nera Rock Band grunge", "Nirvana")
    scarso = _p("Maglietta scura", "Shein")
    neutro = _p("T-shirt uomo Hugo Boss", "Hugo Boss")
    # Il caso che aveva ingannato il ranking: "vintage" nel titolo non è gusto
    generico = _p("T-shirt Ralph Lauren Blu Navy 90s Vintage", "Ralph Lauren")

    assert affinita_gusto(buono, pos, neg) > affinita_gusto(neutro, pos, neg)
    assert affinita_gusto(neutro, pos, neg) > affinita_gusto(scarso, pos, neg)
    assert affinita_gusto(scarso, pos, neg) < 0, "il brand da evitare deve penalizzare"
    assert affinita_gusto(neutro, pos, neg) == 0
    assert affinita_gusto(generico, pos, neg) == 0, "'vintage' da solo non è affinità"
    assert affinita_gusto(buono, pos, neg) > affinita_gusto(generico, pos, neg)

    # Profilo senza gusti: nessun termine, punteggi tutti nulli → l'ordine non cambia
    vuoti_pos, vuoti_neg = vocabolari_gusto(ProfiloUtente())
    assert not vuoti_pos and not vuoti_neg
    assert affinita_gusto(buono, vuoti_pos, vuoti_neg) == 0

    print("OK")
