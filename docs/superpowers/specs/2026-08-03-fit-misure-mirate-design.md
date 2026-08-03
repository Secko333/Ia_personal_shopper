# Consigli mirati sulle misure reali del capo

Data: 2026-08-03

## Problema

Oggi la ricerca filtra per taglia (`L`, `w32 l36`) e il campo `vestibilita` in
`ValutazioneProdotto` è una stima libera di Haiku sulla descrizione del venditore. Una
taglia non dice niente: due t-shirt L hanno lunghezze che vanno da 66 a 78cm, e su Vinted
è il singolo capo usato che conta, non l'etichetta.

L'utente vuole che la richiesta in linguaggio naturale ("una maglietta a manica corta un
po' croppata") venga tradotta in **misure target concrete** derivate dal suo profilo
fisico, e che la ricerca ordini e scarti i capi confrontando quelle target con le misure
reali dichiarate dal venditore.

Esempio dell'utente, che è il caso di test canonico:

> spalle corpo 58 → cerco spalle capo ~52 · petto corpo 102 → cerco petto flat ~51 ·
> croppata → cerco lunghezza ~65

## Cosa è già in piedi

- `vinted_api._descrizione_da_pagina` legge la descrizione del venditore dal
  `<meta name="description">` della pagina pubblica. **Verificato sul campo: non è
  troncato**, contiene la descrizione completa e ben decodificata. Il blob JSON
  `"description":"..."` nella stessa pagina è un'alternativa peggiore (produce mojibake
  con `unicode_escape`). Nessuna modifica necessaria qui.
- `ProdottoRisultato.descrizione` porta già quel testo fino al consulente.
- `ValutazioneProdotto.vestibilita` e la riga `📏` in `display.stampa_risultati`
  esistono già: cambia solo *chi* le riempie.

## Cosa manca

1. La richiesta non viene interpretata in termini di vestibilità.
2. Nessun calcolo delle misure target dal profilo.
3. Le misure del capo non vengono estratte in modo strutturato.
4. L'ordinamento è per prezzo crescente (`aggregatore.filtra_e_ordina`), non per fit.
5. `item["photos"]` viene scartato in `_mappa_item`: si tiene solo la copertina.

## Osservazioni dal campo (verificate live, 2026-08-03)

Su 6 capi reali cercati con "t-shirt vintage misure spalle", **6 dichiaravano le misure**,
in formati tutti diversi:

```
"Larghezza spalle: 53 cm / Torace: 57 cm / Lunghezza (sul retro): 78 cm"
"Spalle: 39 cm / Ascella-ascella: 46 cm / Lunghezza: 58 cm"
"Da ascella a ascella: 51 cm / Da spalla a spalla: 46 cm / Lunghezza: 68 cm"
"Spalle 48cm / Petto 102cm circa / Lunghezza 75cm"
```

Due conseguenze di progetto:

**Estrazione via LLM, non regex.** I sinonimi reali ("ascella/ascella", "da spalla a
spalla", bullet con emoji) fanno perdere al regex una quota consistente dei capi. Un
regex sui pattern sopra ne mancava 1 su 3.

**Normalizzazione circonferenza → flat: è il punto critico.** "Petto 102cm circa" è una
circonferenza; "Torace: 57 cm" è una misura piatta ascella-ascella. Stessa etichetta, due
grandezze. Senza normalizzare, un capo con "Petto 102" (cioè flat 51, *esattamente* il
target dell'utente) verrebbe scartato con uno scarto apparente di +51cm. Regola:
**su una misura di larghezza, valore > 70cm ⇒ è una circonferenza ⇒ /2**.

`item["photos"]` è già presente nella risposta della lista API (5-15 URL full-size,
costo HTTP zero). È la fonte per il fallback vision: molti venditori mettono le misure
solo nelle foto (metro a nastro, o scritte sull'immagine).

## Architettura

### Modulo nuovo: `valutazione/fit.py`

Un solo file, uno scopo: misure del capo contro misure dell'utente. Contiene la tabella
di taratura, l'estrazione, la normalizzazione e il punteggio. Il punteggio resta in Python
— Haiku estrae e commenta, non calcola: ricerche identiche devono dare ranking identico.

#### Misure target

Due assi indipendenti, non un enum unico, così "oversize croppata" resta esprimibile:

- `vestibilita`: `aderente` | `regular` | `oversize` → governa le larghezze
- `lunghezza`: `corta` | `regular` | `lunga` → governa la lunghezza ("croppata" → `corta`)

```python
SPALLE_DELTA = {"aderente": -8, "regular": -6, "oversize": -2}  # spalle_corpo + delta
PETTO_EASE   = {"aderente": -3, "regular":  0, "oversize": +6}  # petto_corpo/2 + ease
VITA_EASE    = {"aderente": -1, "regular": +1, "oversize": +4}  # vita_corpo/2 + ease

COEFF_LUNGHEZZA_TOP = 0.372                                     # altezza -> lunghezza base
LUNGH_OFFSET = {"corta": -8, "regular": 0, "lunga": +5}
```

La lunghezza dei top è derivata dall'altezza (`round(altezza * 0.372) + offset`) invece di
essere una tabella di cm fissi, così resta corretta se l'altezza nel profilo cambia. Su
194cm dà **64 / 72 / 77**. L'utente aveva indicato 65 per la croppata: la derivazione dà
64, uno scarto di 1cm accettato scegliendo la derivazione.

Le costanti stanno in cima al file, sono l'unico punto di taratura, e vanno riviste dopo
le prime ricerche reali.

Verifica sul caso canonico dell'utente (spalle 58, petto 102, altezza 194, "croppata"):
`spalle 58−6 = 52` · `petto 102/2+0 = 51` · `lunghezza 72−8 = 64`.

Se una misura del profilo manca, la target corrispondente è `None` e quella misura non
partecipa né al punteggio né allo scarto.

#### Priorità per tipo di capo

| tipo_capo | peso 3 | peso 3 | peso 1 |
|---|---|---|---|
| `top` | lunghezza | spalle | petto flat |
| `pantaloni` | vita flat | lunghezza interna | coscia |
| `scarpe`, `altro` | — | — | — |

Peso 3 = misura prioritaria: se sfora la soglia il capo viene scartato. Peso 1 = influenza
solo il punteggio, non scarta mai. Questo è esattamente il requisito dell'utente:
"prediligi lunghezza e ampiezza ma meno la larghezza del torace".

Per `scarpe` e `altro` non si fa scoring sulle misure: il filtro taglia già copre le
scarpe, e "altro" è troppo eterogeneo.

#### Punteggio e scarto

```python
SCARTO_MAX_CM = 4     # oltre questo, su una misura prioritaria, il capo è scartato
```

Per ogni misura presente, dallo scarto assoluto rispetto alla target:

| scarto | punteggio misura |
|---|---|
| ≤ 2cm | 1.0 |
| ≤ 4cm | 0.7 |
| ≤ 6cm | 0.4 |
| > 6cm | 0.0 |

Le due bande oltre 4cm restano usate solo dalle misure di peso 1: con
`SCARTO_MAX_CM = 4` una misura prioritaria che le raggiunge fa già scartare il capo.

`punteggio` = media pesata sulle sole misure presenti. `confidenza` = somma dei pesi
presenti / somma dei pesi totali per quel tipo di capo.

Un capo è **scartato** se una misura di peso 3 sfora `SCARTO_MAX_CM`. Un capo **senza
misure dichiarate non viene mai scartato**: passa con `confidenza = 0`, ordinato sotto i
capi misurati, ed etichettato esplicitamente — è il requisito dell'utente ("se non trovi
la misura ma pensi che mi possa stare consigliala uguale ma chiariscilo").

4cm è una soglia severa: sui 6 capi reali del test ne sopravvivevano 1-2. È una scelta
esplicita dell'utente. Mitigazione: la costante è in cima al file e la CLI stampa il
miglior capo scartato con il suo scarto, così si vede subito quando conviene allargare.

#### Estrazione delle misure

Due stadi, `MisureCapo` con un campo `fonte` (`descrizione` | `foto` | `None`):

1. **Descrizioni** — una sola chiamata Haiku su tutti i candidati, output JSON indicizzato
   per posizione (stesso pattern `indice` già usato da `consulente.py`, più robusto che
   ricopiare URL). Normalizzazione circonferenza→flat applicata in Python dopo il parse,
   non delegata al modello.
2. **Foto (fallback)** — solo sui capi che sopravvivono al ranking e a cui manca una
   misura prioritaria. Cap: 6 capi × 3 foto. Si mandano le foto `[2:5]` (le misure stanno
   quasi sempre dopo le prime due di presentazione), o tutte se sono meno di 3.

Il cap e la scelta delle foto sono euristici e vanno tarati: sono marcati `ponytail:` nel
codice con il loro tetto.

Su fallimento di una qualsiasi chiamata LLM, i capi restano senza misure e vengono
etichettati come tali: la ricerca non fallisce mai per colpa dell'estrazione.

### Pipeline

```
richiesta
  → interprete: + vestibilita, + lunghezza                     (prompt esteso)
  → fit.misure_target(profilo, params)                          → mostrate a schermo
  → Vinted: MAX_CANDIDATI_FIT candidati, descrizioni arricchite (~22s)
  → fit.estrai_da_descrizioni(candidati)                        1 chiamata Haiku
  → fit.valuta(): normalizza, punteggia, scarta                 Python puro
  → ordina per (punteggio, confidenza, prezzo) e taglia a 15
  → fit.estrai_da_foto() sui sopravvissuti senza misure         vision, cap 6×3
  → ri-punteggia e taglia ai 12 finali
  → consulente.valuta_prodotti(): riceve target e misure già estratte
  → display
```

`MAX_CANDIDATI_FIT = 60` in `config.py`. Il costo dominante è l'arricchimento delle
descrizioni in `vinted_api.cerca_vinted`: una GET più `time.sleep(0.3)` per capo, quindi
~22s per ricerca. È una scelta esplicita dell'utente per avere un ranking con abbastanza
materiale su cui scegliere. La costante è il knob per rientrare.

L'ordinamento per fit avviene **dopo** `valuta_prodotti` non prima: `filtra_e_ordina`
resta com'è (prezzo crescente) e serve solo a deduplicare e filtrare budget/brand. Il
riordino per fit è un `sorted` in `cli/loop.py` sui `ProdottoArricchito`.

### Output CLI

Prima della ricerca, le target rese esplicite:

```
🎯 Target t-shirt croppata: spalle ~52cm · lunghezza ~64cm · petto ~51cm
```

Dopo, la contabilità di cosa è stato scartato e perché:

```
✓ Vinted: 60 candidati · ✂ 14 scartati fuori misura (il migliore: spalle 47, −5)
📷 4 misure lette dalle foto
```

Nella tabella, la riga `📏` esistente diventa deterministica e mostra lo scarto in cm:

```
#  Prodotto            Prezzo  Parere
1  Carhartt tee L       €22    ✅ COMPRA
                               🎯 SU MISURA  spalle 52 ✓ · lungh 66 ✓ · petto 52 ✓
2  Dickies tee L        €18    🤔 CONSIDERA
                               ● OK  spalle 49 (−3, un po' stretto) · lungh 67 ✓ · petto 55 (+4)
3  Nike tee L           €15    🤔 CONSIDERA
                               📏 misure non dichiarate — valutato solo su taglia L
```

La riga 2 mostra il caso limite tenuto: `spalle −3` sta dentro `SCARTO_MAX_CM`, e
`petto +4` sfora ma è peso 1, quindi non scarta.

## Modelli dati

```python
class MisureCapo(BaseModel):
    spalle_cm: float | None = None
    petto_flat_cm: float | None = None
    lunghezza_cm: float | None = None
    vita_flat_cm: float | None = None
    lunghezza_interna_cm: float | None = None
    fonte: str | None = None            # "descrizione" | "foto" | None

class MisureTarget(BaseModel):
    spalle_cm: float | None = None
    petto_flat_cm: float | None = None
    lunghezza_cm: float | None = None
    vita_flat_cm: float | None = None
    lunghezza_interna_cm: float | None = None

class EsitoFit(BaseModel):
    punteggio: float                    # 0..1, media pesata sulle misure presenti
    confidenza: float                   # 0..1, quota di peso coperta da misure note
    scartato: bool
    motivo_scarto: str | None = None    # "lunghezza 78 vs 64 (+14)"
    dettaglio: str                      # "spalle 52 ✓ · lungh 66 ✓ · petto 52 ✓"
```

`ParametriRicerca` guadagna `vestibilita: str = "regular"` e `lunghezza: str = "regular"`.
`ProdottoArricchito` guadagna `misure: MisureCapo | None` e `fit: EsitoFit | None`.

I default `"regular"` fanno sì che una richiesta senza indicazioni di vestibilità
("cerca una t-shirt nera") produca comunque target sensate invece di disattivare il fit.

## File toccati

| file | modifica |
|---|---|
| `valutazione/fit.py` | **nuovo** — tabella, estrazione, normalizzazione, punteggio, self-check |
| `models.py` | `MisureCapo`, `MisureTarget`, `EsitoFit`; campi su `ParametriRicerca` e `ProdottoArricchito` |
| `ricerca/interprete.py` | prompt: estrae i due assi di vestibilità |
| `vinted_api.py` | `_mappa_item` tiene `photos`; `per_page` dal nuovo cap candidati |
| `ricerca/coordinatore.py` | passa il numero di candidati a `cerca_vinted` |
| `valutazione/consulente.py` | il prompt riceve target e misure estratte; non stima più `vestibilita` |
| `cli/loop.py` | orchestrazione: target → ricerca → estrazione → punteggio → vision → riordino |
| `cli/display.py` | riga fit deterministica, riga target, contabilità scarti |
| `config.py` | `MAX_CANDIDATI_FIT = 60` |

## Test

Un `__main__` con `assert` in `valutazione/fit.py`, senza framework, che copre la logica
non banale su dati reali già raccolti:

- il caso canonico dell'utente: profilo (58, 102, 194) + "croppata" → target (52, 51, 64)
- normalizzazione: `petto 102 → 51`, `petto 57 → 57` (sotto soglia, resta flat)
- scarto su misura prioritaria: Nike `lungh 78` vs 64 (+14) → scartato; Armani
  `spalle 46` vs 52 (−6) → scartato; Hardrock `lungh 75` vs 64 (+11) → scartato
- caso limite tenuto: `spalle 49` vs 52 (−3) → non scartato, punteggio misura 0.7
- peso 1 fuori soglia non scarta mai: `petto 60` vs 51 (+9) → `scartato = False`
- capo senza misure → `scartato = False`, `confidenza = 0`
- misura di profilo mancante → target `None`, esclusa da punteggio e scarto
- assi combinati: "oversize croppata" → `spalle 56`, `petto 57`, `lunghezza 64`

La parte LLM (estrazione da descrizioni e foto) richiede rete e chiave API: gira solo se
`ANTHROPIC_API_KEY` è presente, come già fa `interprete.py`.

## Fuori scopo

- Zara e Zalando: non espongono le misure del singolo capo, restano sul filtro taglia.
  Il punteggio fit li tratta come "misure non dichiarate".
- Filtro per misure lato API Vinted: non esiste, il ranking è necessariamente post-fetch.
- Apprendimento delle costanti di taratura dal feedback (`/mipiace`): le costanti si
  correggono a mano nel file.
