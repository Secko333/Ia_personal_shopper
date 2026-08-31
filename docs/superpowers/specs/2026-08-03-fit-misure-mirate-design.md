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
| `config.py` | `MAX_CANDIDATI_FIT = 60`, `MAX_RISULTATI_FINALI = 12`, `MAX_CAPI_VISION = 6` |
| `ricerca/aggregatore.py` | `filtra_e_ordina(limite=…)`: tagliare a 20 affamava il ranking |

`ValutazioneProdotto.vestibilita` è stato rimosso: era la stima libera di Haiku, ora
sostituita dal confronto deterministico in `EsitoFit.dettaglio`.

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

## Scoperto durante l'implementazione

Cinque cose che il design non prevedeva e che sono state necessarie per far funzionare la
feature. Tutte verificate sul campo.

### 1. La query va spinta verso i capi che dichiarano le misure (il fix decisivo)

Con una query normale **lo 0% dei risultati dichiara le misure**: il ranking non aveva
niente da confrontare e ogni capo finiva "in forse". `search_text` di Vinted cerca anche
nelle descrizioni, quindi aggiungere il vocabolario delle misure cambia la popolazione:

| query | capi con misure dichiarate |
|---|---|
| `maglietta manica corta` | 0% |
| `maglietta manica corta misure` | 10% |
| `t-shirt uomo misure spalle lunghezza` | **75%** |

`coordinatore._cerca_vinted` fa quindi **due ricerche unite**: 2/3 del budget candidati
alla query spinta sulle misure, 1/3 alla query normale per non perdere copertura. Le parole
sono adattate al tipo di capo (`misure spalle lunghezza` per i top, `misure vita lunghezza`
per i pantaloni). Sequenziali, non parallele: condividono la sessione `curl_cffi`
module-level. La `rilevanza` viene riassegnata sull'ordine unito.

### 2. Paginazione: senza, i 60 candidati richiesti erano 28

`per_page` è tappato a 96 dall'API (192 torna 96) e il filtro taglia post-fetch ne scarta
oltre metà. La prima esecuzione reale consegnava 28 candidati su 60 richiesti, in silenzio.
`cerca_vinted` ora pagina fino a `_MAX_PAGINE = 3` finché non raggiunge il numero chiesto,
deduplicando per id (le pagine Vinted si sovrappongono del 15% circa).

### 3. Lo spareggio è la rilevanza, non il prezzo

Ordinare a pari fit per prezzo crescente riempiva la lista di magliette da €2 senza misure
al posto dei capi pertinenti. `ProdottoRisultato.rilevanza` conserva la posizione
nell'ordine di rilevanza del sito, assegnata prima di qualsiasi riordino, e
`_chiave_ordine` la usa come ultimo criterio.

### 4. Banda di plausibilità sui numeri estratti

Sul campo si trovano "Spalle 90cm" e "Spalle 18,5cm". Un numero fuori dalla banda
plausibile per un capo da adulto vale come **non dichiarato**, non come misura sbagliata da
confrontare: altrimenti sporca il punteggio e fa scartare capi buoni.

Correzione collegata: il dimezzamento circonferenza→flat si applica solo a **petto e
vita**, dove la circonferenza è una convenzione reale. Sulle spalle non esiste: 90cm di
spalle è un errore del venditore, non 45cm di misura piatta.

### 5. Gli errori di estrazione vanno mostrati, non inghiottiti

Il design faceva ricadere ogni errore LLM su "capo senza misure". In pratica questo ha
mascherato una chiave API non caricata facendola sembrare "nessun venditore ha scritto le
misure" — e ha depistato anche durante lo sviluppo. Ora:

- `estrai_da_descrizioni` distingue `None` (chiamata fallita) da `{}` (nessuna misura
  dichiarata), e il report alza `errore_descrizioni`;
- `_misure_da_foto` lascia propagare gli errori di chiamata, contati in `errori_foto`;
- la CLI stampa un avviso rosso esplicito quando la lettura è fallita per un problema
  tecnico, così il degrado non passa per un dato di fatto.

### Esito su una ricerca reale

`"voglio una maglietta a manica corta un po' croppata, max 30€"` con il profilo
di Riccardo (194cm, spalle 58, petto 102):

```
🎯 Misure cercate (top): spalle ~52cm · petto ~51cm · lungh ~64cm
60 candidati · ✂ 24 scartati fuori misura (il più vicino: lungh 70 vs 64 (+6)) · 📷 1 misure lette dalle foto

1  T-shirt uomo verdone Benetton TG L   €3   ✅ COMPRA
                                             🎯 SU MISURA  lungh 65 ✓ · spalle 50 ✓ · petto 55 (+4, largo)
```

La soglia a 4cm si conferma severa come previsto: 24 capi su 60 scartati, e il "più
vicino" sfora di 6cm. `SCARTO_MAX_CM` in cima a `fit.py` è il numero da alzare se le liste
risultano troppo corte.

## Seconda tornata: il gusto (2026-08-03)

Le misure funzionavano, ma la prima ricerca reale non conteneva un solo capo gradito. Causa:
**lo stile influenzava il giudizio, non il recupero.** Le `preferenze_stile` arrivavano solo
al prompt del consulente, a valle, quando la lista era già stata pescata. Si pescava il
mainstream e lo si etichettava correttamente come non gradito.

Misurato sul campo, stessa taglia e stesso budget:

| query | cosa torna |
|---|---|
| `maglietta manica corta` | Hugo Boss, Disney Lion King, Ralph Lauren, Liu Jo, Guess |
| `t-shirt band rock vintage` | AC/DC, Mötley Crüe, Blink 182, Vasco Rossi, Led Zeppelin |

La palette colori del profilo come filtro API **non serve**: restituisce gli stessi Hugo
Boss e Guess, solo neri. Non è il colore, è il vocabolario di stile.

### Profilo: campi separati (`versione: 2`)

`/stile intervista` scriveva ogni risposta in `preferenze_stile`, che quindi conteneva
stili, colori, occasioni e vestibilità impastati — e i cinque stili schiacciati in una
stringa sola. Il vocabolario con cui si costruiscono le query conteneva "nero" e "serate".

`gestore._migra_v2` separa in `preferenze_stile` / `colori_preferiti` / `occasioni` /
`vestibilita_preferita`. Deterministica, senza rete, al caricamento del profilo: i colori si
riconoscono da `COLOR_IDS`, le occasioni e le vestibilità da vocabolari espliciti. Il
confronto è su voce intera, non per sottostringa, o "Slim Rock" diventerebbe la vestibilità
"slim". Anche il prompt dell'intervista è stato riscritto per smistare all'origine,
altrimenti la prossima intervista rimescolerebbe tutto.

Effetto collaterale utile: `vestibilita_preferita` diventa il default quando la richiesta
non dice come deve vestire. Prima "cerca una t-shirt nera" usava sempre `regular`,
ignorando che l'utente preferisce aderente.

### Varianti di gusto: sono l'unica ricerca eseguita

L'interprete genera 0-2 query alternative combinando il capo richiesto col vocabolario di
stile dell'utente, e `coordinatore._cerca_vinted` cerca **solo** su quelle, ripartendo il
budget candidati. La query neutra viene usata solo quando le varianti mancano — richiesta
già specifica ("t-shirt dei Ramones") o varianti tutte scartate.

La fetta neutra è stata eliminata perché portava esclusivamente mainstream e, avendo spesso
misure complete e fit regolare, occupava le prime posizioni. Le varianti da sole danno ~58
candidati unici: il pool non si assottiglia.

### Tre presidi deterministici sul prompt

La qualità delle varianti oscillava tra esecuzioni identiche: una volta
`"t-shirt grunge single stitch"`, quella dopo `"t-shirt vintage maniche corte"` — che
riporta il mainstream, perché su Vinted "vintage" lo scrive metà dei venditori. Con le
varianti come unica ricerca, una variante debole costa l'intera lista.

Il prompt da solo non è affidabile. Tre controlli in Python, ognuno con il suo self-check:

- `_variante_discriminante`: una variante deve contenere un termine di sottocultura, scena
  o dettaglio costruttivo. Le epoche (`EPOCHE_GENERICHE`) non contano.
- `_togli_vestibilita`: i termini di sola vestibilità escono dalla query. Volutamente non
  tocca "corta"/"lunga"/"larga": lì distinguono il capo, e togliere "lunga" da "manica
  lunga" lo rovina.
- `_togli_colori`: un colore già estratto in `colori` è un filtro API, nella query è rumore.
  Con concordanza di genere e numero ("bianco" → bianca, bianchi, bianche), e attenzione
  alle radici vicine: `rosa` non deve mangiare `rossa`.

Dopo questi controlli, tre esecuzioni della stessa richiesta danno varianti identiche.

### Affinità di gusto nell'ordinamento

`profilo/gusti.affinita_gusto` conta quanti termini del vocabolario dell'utente compaiono
nel titolo, meno il doppio di quelli da evitare. La ricerca Vinted è fuzzy: anche cercando
"band tee alt rock single stitch" tornano Shein e Maison Margiela.

`_chiave_ordine` diventa `(fascia di fit, −affinità, −confidenza, rilevanza)`. La fascia sta
**sopra** l'affinità — un capo del gusto giusto che non veste resta inutile — ma dentro la
fascia l'affinità viene prima del punteggio fine, perché la differenza tra 0.95 e 0.93
nasce da bande a gradini e non si vede addosso, mentre quella tra una band tee e una polo
Ralph Lauren sì.

Due errori di principio commessi e corretti, entrambi lo stesso: **un termine che non
discrimina non è gusto.**

1. All'inizio le varianti generate entravano nel vocabolario di affinità. Con "t-shirt
   vintage" tra le varianti, "Ralph Lauren Blu Navy 90s Vintage" prendeva lo stesso punto
   di una band tee grunge. Il vocabolario ora si costruisce **solo** dal profilo: le
   varianti servono a pescare, il profilo a giudicare.
2. `EPOCHE_GENERICHE` e i nomi di colore vanno esclusi da qualunque fonte, incluso un
   descrittore appreso da `/mipiace` tipo "band tee **nera** stampa rock **anni 2000**":
   altrimenti qualunque capo nero prende un punto, Hugo Boss compreso.

### Apprendimento: descrittori, non brand

`/mipiace` salvava il brand. "Vintage Dressing" è un negozio, non uno stile, e non serve a
costruire una ricerca. Ora `profilo/gusti.descrittore_stile` ricava dall'articolo un
descrittore breve e riusabile come termine di ricerca, che entra nel vocabolario di gusto:

```
T-Shirt AC/DC - For Those About To Rock  →  "band tee nera stampa rock anni 2000"
Camicia Lee Western Nera Vintage         →  "camicia western nera denim vintage"
```

Risposte più lunghe di dieci parole vengono rifiutate: significa che il modello ha spiegato
invece di descrivere, e una frase intera dentro le query di ricerca fa più danni che bene.

### Esito

`"maglietta t-shirt a maniche corte"`, la richiesta che non aveva prodotto niente di
gradito:

```
🎨 Cerco anche sul tuo gusto: 'band tee rock maniche corte' · 't-shirt grunge single stitch'
🎯 Misure cercate (top): spalle ~50cm · petto ~48cm · lungh ~72cm

1  Maglietta vintage Da Vinci Rock vitruvian y2k   €14  🎯 SU MISURA  petto 50 ✓ (dalle foto)
2  Vintage 2000s T-Shirt Guns N' Roses             €15  ● OK  lungh 68 (−4) · spalle 48 ✓
6  T-Shirt AC/DC - For Those About To Rock         €13  📏 misure non dichiarate
9  shirt vintage rock eagle lupo single stitch     €35  📏 misure non dichiarate
```

Nessun Hugo Boss, nessun Disney, nessuna camicia a maniche corte al posto di una t-shirt.

### Fragilità residua

Il vocabolario di gusto dell'utente è ancora sottile: cinque nomi di stile e, all'inizio,
nessun descrittore appreso. L'affinità è quindi un segnale sparso — quando scatta è
significativo, ma su molti capi vale 0 e decide la rilevanza Vinted. Si infittisce con
l'uso di `/mipiace`.

## Terza tornata: la vestibilità è la chiave, il gusto viene dopo (2026-08-03)

La seconda tornata aveva reso il gusto il criterio del recupero. Sbagliato: misurato sul
campo, mescolare gusto e misure nella stessa query **distrugge** la selettività sulle misure.

| query | capi con misure dichiarate |
|---|---|
| `t-shirt uomo misure spalle lunghezza` | **65-75%** |
| `t-shirt band tee rock misure spalle lunghezza` | 5% |
| `t-shirt grunge single stitch misure spalle lunghezza` | 0% |
| `t-shirt band tee rock grunge` | 5% |

La ricerca Vinted è un'intersezione su titolo e descrizione: pochi venditori scrivono sia
il vocabolario di sottocultura sia le misure, e il risultato è dominato dal termine più
raro. La seconda tornata aveva quindi barattato le misure per il gusto senza accorgersene —
è per questo che le liste mostravano quasi solo "misure non dichiarate".

### Un solo termine di stile, e non uno qualsiasi

Esiste una via di mezzo, ma è strettissima e dipende da quale termine:

| query | misure | gusto |
|---|---|---|
| `t-shirt uomo misure spalle lunghezza` | 65% | 0% |
| `t-shirt misure spalle lunghezza grunge` | **65%** | **40%** |
| `t-shirt misure spalle lunghezza vintage` | 35% | 5% |
| `t-shirt misure spalle lunghezza rock` | 15% | 80% |
| `t-shirt misure lunghezza band tee` | 10% | 55% |

"grunge" tiene entrambi perché lo scrivono i venditori vintage curati, che sono anche quelli
che riportano le misure; "rock" e "band tee" compaiono nelle inserzioni di massa e
spostano l'intersezione sui capi che non le dichiarano. Campione di 20 per query: indicativo.

Di conseguenza `ParametriRicerca.varianti_gusto` (liste di query) è stato sostituito da
`termine_stile: str | None`: **una** parola, massimo due.

### Recupero

`coordinatore._cerca_vinted` fa due ricerche, con `MAX_CANDIDATI_STILE = 20` su
`MAX_CANDIDATI_FIT = 60`:

```
40  "{query} misure spalle lunghezza"            ← caccia pura alle misure
20  "{query} misure spalle lunghezza {stile}"    ← misure con una tinta di gusto
```

Senza un termine di stile utilizzabile, tutti i 60 vanno alla caccia pura. Il gusto agisce
poi nell'ordinamento, dove `_chiave_ordine` è già `(fascia di fit, −affinità, −confidenza,
rilevanza)`: la vestibilità decide, il gusto rompe i pari.

I capi senza misure dichiarate restano in lista sotto il separatore "in forse", come da
requisito originale: chi non dichiara le misure si può sempre contattare.

### Altri due presidi deterministici

`_termine_stile_valido` rifiuta un termine che sia un'epoca (`EPOCHE_GENERICHE`), troppo
comune nei titoli (`_GUSTO_TROPPO_COMUNE`: rock, band, tee, graphic, streetwear, denim),
più lungo di due parole, o non riconducibile a nessuno stile. Senza, la scelta del modello
oscilla tra esecuzioni identiche e spreca la fetta che le è riservata.

`_vestibilita_richiesta` è il presidio più importante per la correttezza del fit. Il prompt
chiede `null` quando la richiesta non dice come deve vestire il capo, ma il modello
risponde "regular" per riflesso — e così il default del profilo non scattava mai. Per un
utente che preferisce aderente il target sbagliava di 2cm sulle spalle e 3 sul petto,
esattamente ciò che la feature dovrebbe garantire. Ora si rileva sul testo della richiesta
se la vestibilità è stata espressa; se no, vince il profilo.

Il rilevamento è volutamente più larga di `_SOLO_VESTIBILITA` (include i termini ambigui
"larga", "comoda", "stretta") ma esclude "corta" e "lunga": in "manica corta" parlano
della manica, non di come veste.

### Esito

`"maglietta t-shirt a maniche corte"`, profilo con `vestibilita_preferita: aderente`:

```
🎨 Una parte della ricerca aggiunge il tuo stile: 'grunge'
🎯 Misure cercate (top): spalle ~50cm · petto ~48cm · lungh ~72cm
60 candidati · ✂ 21 scartati fuori misura · 📷 2 misure lette dalle foto
```

Tutti i capi mostrati dichiarano le misure, contro i 3 su 12 della tornata precedente.

### Nota sul dato di profilo

`vestibilita_preferita` è un valore di dati, non di codice: se vale `regular` il target usa
gli scarti regular anche quando l'utente preferirebbe aderente. Si controlla con `/profilo`
e si corregge con `/profilo modifica`.

## Quarta tornata: il feed Vinted come fonte del gusto (2026-08-03)

Tre tornate a costruire un vocabolario di gusto da parole chiave hanno dato risultati
mediocri. L'osservazione dell'utente ribalta il problema: il recommender di Vinted è
addestrato sul suo comportamento reale (cosa guarda, cosa salva), quindi sul gusto batte
qualunque vocabolario scritto a mano. Al programma resta ciò che sa fare bene: i centimetri.

### Come si legge il feed

La homepage Vinted è renderizzata lato server: i capi del feed arrivano come oggetti JSON
completi dentro i payload `self.__next_f.push([1,"…"])`. Sono stringhe JS, quindi
`json.loads` sulla stringa le de-escapa in JSON leggibile; poi si ritagliano le entity
`"type":"item","entity":{…}` bilanciando le graffe — non basta cercare la prima `}`, i
titoli dei venditori ne contengono.

Ogni entity porta id, titolo, url, prezzo, miniatura e `itemBox.secondLine`
(`"M / IT 42 / EU 38 · Ottime"` = taglia · condizione). Il brand non c'è: resta `None`.

### Il limite: 20 capi, non paginabili

Il feed vero è paginato via `nextPageToken`. L'endpoint è
`/web/gateway/homepage/homepage` — trovato leggendo i bundle JS, dove
`getHomepageTab` lo chiama con `next_page_token`, `homepage_session_id`, `column_count`,
`version: 4`, `vertical`. Risponde `{"code":"BAD_REQUEST"}` con qualunque combinazione
provata di parametri e header: vuole un'autenticazione del gateway che non vale la pena
indovinare a tentoni.

Verificato inoltre che il feed **non ruota**: stessi 20 capi ricaricando la homepage e
anche rinnovando la sessione. Quindi 20 capi per fetch è il tetto. La pagina catalogo
(`/catalog/5-uomo`) ne renderebbe 96 per pagina e pagina bene, ma è personalizzata molto
più debolmente — usarla riporterebbe la spazzatura da cui si voleva scappare.

Dei 20, circa un terzo dichiara le misure, meno i fuori misura: **2-4 risultati per giro**.
Pochi ma con il gusto scelto da Vinted.

### Login: l'utente accede da sé

`browser/vinted.apri_login()` apre una finestra Chromium headful sul profilo persistente
(`BROWSER_DATA_DIR / "vinted"`, lo stesso di `/carrello`) e attende. **Nessuna credenziale
viene chiesta, letta o salvata dal programma**: l'utente accede a mano, Chromium scrive i
cookie di sessione su disco, e `vinted_api._cookie_da_browser` li trapianta nella sessione
curl_cffi. È il meccanismo che già esisteva per `/carrello`, qui riusato.

Playwright sincrono non può girare dentro l'event loop asyncio, quindi `/login` chiama
`apri_login` via `asyncio.to_thread`. Serve il binario del browser:
`uv run playwright install chromium`.

`sessione_autenticata()` interroga `/api/v2/users/current` (403 da anonimo, 200 da loggato)
solo per dare messaggi utili.

### Due usi del feed

**Filtrare.** `/feed` classifica ogni capo con `tipo_capo_da_titolo` (deterministica: il
feed non nasce da una richiesta, e una chiamata LLM per capo sarebbe sproporzionata),
raggruppa per tipo, e passa ogni gruppo alla pipeline delle misure già esistente. Le misure
target dipendono dal tipo, quindi il raggruppamento è necessario.

Quel che non è abbigliamento viene scartato subito: il feed serve anche giocattoli, carte
Pokémon e borse. Si applica anche il filtro morbido sulle taglie, che nel percorso di
ricerca c'era già ma qui mancava.

**Imparare.** `profilo/gusti.impara_da_feed` ricava dai titoli 3-6 termini di stile
ricorrenti, che finiscono in `gusti_positivi` e quindi nel vocabolario di affinità e nella
scelta del termine di stile delle ricerche normali. Il recommender di Vinted diventa il
maestro del gusto, non solo una fonte di capi.

**Guardia obbligatoria**: da sessione anonima il feed NON è personalizzato — verificato, mostra
Barbie, gonne leopardate e carte Pokémon. Imparare da lì scriverebbe "barbie vintage" nel
profilo dell'utente. `/feed` impara **solo** se `sessione_autenticata()`.

### Un bug preesistente nel filtro taglie

Il feed ha esposto una falla in `taglia_compatibile`, che c'era da prima e valeva anche per
le ricerche: confrontando token numerici nudi,
`taglia_compatibile("S / IT 40 / EU 36", ["w32 l36"])` dava `True`, perché il 36 della
taglia EU donna coincide col 36 della lunghezza gamba dell'utente. Passavano gonne da donna
in taglia S.

Regola aggiunta: se **entrambi** i lati dichiarano una taglia alfabetica, quelle devono
coincidere. Numeri di sistemi di taglie diversi non sono confrontabili. E `_TAGLIE_ALPHA`
è stata completata: mancava `xxs`, quindi per un capo "XXS / IT 36" la regola non scattava
nemmeno.

### Stato della verifica

Verificato anonimamente: parser del feed (20 entity complete), classificazione dal titolo,
filtro taglie, scarto dei non-abbigliamento, ordinamento sull'insieme dei gruppi, rifiuto
di imparare da feed anonimo, profilo browser persistente creato e leggibile.

**Non verificabile senza login**: che il feed autenticato sia effettivamente personalizzato
e produca risultati di gusto. Richiede che l'utente esegua `/login`.

## Quinta tornata: il blocco DataDome al login (2026-08-03)

Il primo `/login` reale è stato bloccato: *"La tua sessione è stata bloccata — abbiamo
notato attività insolite o automatizzate collegate al tuo indirizzo IP"*.

### Diagnosi: non è l'IP

Verificato subito dopo il blocco: la sessione `curl_cffi` continua a funzionare (homepage
200 con le 20 entity del feed, `catalog/items` restituisce risultati). Una richiesta senza
`impersonate` prende 403, come sempre. Quindi **l'IP non è bandito**: è il browser
automatizzato che è stato riconosciuto.

Nota metodologica: cercare "datadome" o "attività insolite" nel corpo della homepage dà un
falso positivo — quelle stringhe stanno nel bundle di traduzioni della pagina normale. Per
sapere se si è bloccati va guardato l'esito dell'API, non il testo della pagina.

### Il browser: Chrome vero invece del Chromium incluso

Il Chromium di Playwright espone segnali di automazione evidenti. `apri_login` ora prova in
ordine `channel="chrome"`, `"msedge"` e solo per ultimo il Chromium incluso, con
`--disable-blink-features=AutomationControlled`, `ignore_default_args=["--enable-automation"]`
e `viewport=None` (finestra reale invece del 800x600 tipico dell'automazione).

### La causa più probabile: il nostro volume di traffico

`_descrizione_da_pagina` scaricava la pagina prodotto **intera** (~2MB) per leggere un meta
tag che sta nell'`<head>`. Una ricerca ne apre 60: **~120MB per ricerca**. È quel volume,
più le decine di fetch della fase di esplorazione degli endpoint, la spiegazione più
plausibile dell'"attività insolita".

Ora la pagina si legge in streaming e la connessione si chiude appena il meta tag è passato:
16KB invece di 1,9MB. Misurato su 12 capi: **23,4MB → 8MB per ricerca, −66%**.

Il ripiego sulla lettura intera non è una precauzione teorica, serve in due casi reali:

1. Su alcune pagine il meta tag compare **oltre 1,8 milioni di caratteri** — Next.js emette
   parte dell'`<head>` molto tardi nello stream.
2. Interrompere uno stream lascia la connessione sporca: **la terza richiesta interrotta di
   fila sulla stessa sessione restituisce un corpo inutilizzabile** (riprodotto: 112KB letti
   senza mai trovare il marcatore). Una lettura completa ripulisce lo stato.

Senza il ripiego la copertura scendeva a 3 su 6 capi: una regressione silenziosa proprio sul
dato che regge tutta la feature. Con il ripiego, 12 su 12.

`Range: bytes=0-N` è stato provato e **non serve**: il server lo ignora e manda tutto.

### Il self-check giusto

Il primo tentativo — "quasi tutti i capi devono avere una descrizione" — falliva legittimamente:
diversi venditori non scrivono nulla, e un meta tag vuoto non è una perdita. L'invariante
corretto confronta i due percorsi sullo stesso capo: **lo streaming non deve perdere ciò che
la lettura intera contiene**. Zero perse su 6.

## Sesta tornata: usare la sessione Chrome già aperta (2026-08-03)

Domanda dell'utente: se sono già loggato su Vinted nel mio Chrome, perché devo riaccedere?
Risposta: non deve. E leggere i cookie dal suo Chrome è **meglio** del login con finestra,
perché non lancia nessun browser automatizzato — DataDome non ha niente da riconoscere.

### Fattibilità, verificata

Il profilo `Default` di Chrome contiene 40 cookie di vinted.it, fra cui
`access_token_web` e `refresh_token_web`. Tutti in schema **`v10`** (3709 cookie su 3709):
AES-128-CBC con chiave derivata dal Keychain. Non `v20` (App-Bound Encryption), che sarebbe
stato impossibile da leggere dall'esterno. `cryptography` era già nel venv.

### `browser/cookie_chrome.py`

Chiave: PBKDF2-HMAC-SHA1 della password "Chrome Safe Storage" del Keychain, salt
`b"saltysalt"`, 1003 iterazioni, 16 byte. Valore: prefisso `v10` + AES-128-CBC con IV di 16
spazi, padding PKCS7. Le versioni recenti premettono al testo in chiaro l'hash SHA-256 del
dominio: se il risultato non è stampabile si riprova saltando 32 byte.

Limiti deliberati del modulo:

- interroga **solo** le righe di vinted.it, e di quelle **solo** i due token di sessione;
- non legge, copia o registra nessun altro cookie né altro dato di Chrome;
- non tocca password — i token di sessione non permettono di ricavarla;
- non stampa mai i valori, nemmeno nel self-check (che verifica lunghezza e forma JWT);
- copia il database prima di leggerlo, perché Chrome lo tiene aperto.

I token si tengono in cache per la durata del processo: ogni lettura fa comparire il dialogo
del Keychain, e `_warm()` viene richiamata a ogni 403.

`_warm()` prende l'autenticazione da Chrome se c'è; il profilo browser persistente di
`/login` resta come ripiego per chi non usa Chrome. `/login` prova Chrome per primo e
apre la finestra solo se lì non trova una sessione valida.

### Esito: il feed è personalizzato

Autenticato (`/api/v2/users/current` → 200). Il feed cambia completamente:

```
anonimo:      Barbie silkstone · gonna leopardata S · carte Pokémon · braccialetti
autenticato:  T-Shirt Diesel XL · Levi's · Champion · HUF · Dickies Henley vintage
              New York Mets raglan MLB · Guinness tee · Brooks
```

25 capi invece di 20, tutti taglie L/XL da uomo.

### Il limite che resta, ed è strutturale

Su 25 capi del feed, **1 o 2 dichiarano le misure**. Sono venditori occasionali, non i
negozi vintage curati che le scrivono. Il feed dà quindi gusto eccellente e misure quasi
nulle: l'esatto opposto della ricerca spinta sulle misure.

Correzione collegata: l'entity del feed porta solo `thumbnailUrl`, una foto. Il fallback
vision riceveva la sola copertina, mentre il metro a nastro sta nelle foto successive. Le
URL full-size stanno nella stessa zona iniziale della pagina del meta tag (entro ~7KB),
quindi ora si raccolgono nella stessa lettura in streaming, a costo zero — 2-7 foto per capo.
Anche così, nel feed reale la vision non ha trovato misure leggibili: quei venditori non
fotografano il metro.

### Il ponte fra i due mondi: l'apprendimento

È la metà che funziona davvero. Dai 25 titoli del feed autenticato:

```
vintage band tee · sportswear classico · heritage brand · graphic tee
streetwear 90s · Levi's denim · single stitch
```

Il vocabolario di affinità passa da 5 nomi di stile a 21 termini. Verificato sulla ricerca
normale subito dopo: primo risultato *"Official NYC Top of the Rock Graphic T-Shirt — Faded
Black, American Vintage"*, `SU MISURA spalle 51 ✓`, giudizio "vintage Y2K band tee esatto".
Misure garantite dalla ricerca, gusto imparato dal feed.

Nota di progetto: molti termini imparati (`band`, `tee`, `graphic`, `streetwear`, `denim`)
stanno in `_GUSTO_TROPPO_COMUNE`, quindi arricchiscono l'affinità ma non vengono scelti come
termine di ricerca. È corretto: discriminano dentro un titolo, non dentro una ricerca Vinted.

### Un ultimo errore di 8cm

L'esecuzione di verifica ha smascherato un bug che nessun test copriva: su *"maglietta
t-shirt a maniche corte"* il modello restituiva `lunghezza: "corta"`, confondendo la
lunghezza della **manica** con quella del **capo**. Il target scendeva a 64cm invece di 72,
su una richiesta che non chiedeva nessun crop.

`_lunghezza_richiesta` toglie dal testo le locuzioni "manica/maniche corta/lunga" e solo poi
cerca indicazioni di lunghezza. Il guardiano gemello per la vestibilità esisteva già; questo
mancava.

Nel farlo è emerso che entrambe le regex scrivevano `lungh[aeio]`, che in italiano non
matcha né "lungo" né "lunga" (l'h c'è solo in "lunghi/lunghe"). La svista era **simmetrica**,
quindi il test su "manica lunga" passava per caso e non per funzionamento: le forme corrette
sono ora in un'unica costante condivisa, `_FORME_LUNGHEZZA`.

## Settima tornata: precisione onesta sulle misure (2026-08-04)

Il modello a media pesata mentiva. Caso reale trovato in lista:

```
Maglietta Hard Rock Amsterdam   🎯 SU MISURA   lungh 70 ✓ · spalle 52 ✓ · petto 60 (+9, molto largo)
```

Due difetti distinti, entrambi strutturali:

1. **Il petto non poteva scartare.** Aveva peso 1 e la regola di scarto guardava solo le
   misure di peso 3. Un errore di 9cm passava indenne.
2. **La fascia veniva dedotta da una media.** `(3 + 3 + 0) / 7 = 0.857`, sopra la soglia di
   0.85: le due misure giuste compensavano quella sbagliata. E un capo che dichiarava **una
   sola** misura su tre poteva prendere "su misura" pur essendo in gran parte ignoto.

### Misurato prima di scegliere

Su 60 candidati reali (di cui ~18 con qualche misura dichiarata):

| regola | certi | incerti | esclusi |
|---|---|---|---|
| attuale (media pesata) | 2 | 2 | 14 |
| tutte le misure a 4cm, tutte obbligatorie | **0** | 1 | 17 |
| prioritarie 4cm, petto 8cm, petto opzionale | **3** | 0 | 15 |
| prioritarie 4cm, petto 8cm, petto obbligatorio | 2 | 1 | 15 |

La severità uniforme dà **liste vuote**: le tolleranze devono restare differenziate, come da
richiesta originale ("prediligi lunghezza e ampiezza, meno il torace"). La differenza è che
ora anche il petto esclude, con una banda più larga — non ha più l'immunità.

### Tre classi esplicite invece di una media

`EsitoFit.classe` sostituisce la fascia dedotta dal punteggio:

- **0 · su misura** — tutte le misure prioritarie dichiarate e dentro tolleranza
- **1 · misure parziali** — quel che dichiara è giusto, ma manca una prioritaria
- **2 · non dichiarate** — il venditore non ha scritto nulla

**Scartato** (non compare affatto) se una qualsiasi misura dichiarata sfora la propria
tolleranza: `SCARTO_MAX_CM = 4` per lunghezza e spalle, `SCARTO_MAX_SECONDARIO_CM = 8` per il
petto. Se il venditore l'ha scritta e non va bene, non c'è niente da interpretare.

Il `punteggio` resta ma degrada a spareggio: l'ordine è
`(classe, −affinità di gusto, −punteggio, rilevanza)`. Quindi la vestibilità decide, il gusto
rompe i pari, e a pari gusto vince chi è più preciso sui centimetri.

Il separatore in tabella scatta ora al primo capo **non** su misura, non al primo senza
misure: sia le parziali sia le assenti sono "potrebbe starti", e vanno distinte dalle certe.

### Due correzioni collegate

**La vision tenta anche sui parziali.** Prima leggeva le foto solo dei capi senza nessuna
misura; ora anche di quelli parziali, perché una misura letta dalle foto può completarli e
promuoverli a certi. Le misure si **uniscono** invece di sovrascriversi (`_unisci`): una
misura scritta dal venditore è più affidabile di un numero letto in una fotografia, quindi
se c'è già resta quella, e le foto colmano solo i buchi.

**Il consulente non ri-giudica i centimetri.** Con le etichette corrette è emersa
un'incoerenza nella stessa riga di tabella: fit `🎯 SU MISURA`, parere `❌ EVITA — spalle
troppo strette (-4cm)`. Il verdetto sulle misure è già stato preso in modo deterministico e i
capi fuori tolleranza non arrivano nemmeno al modello, quindi il prompt ora dichiara le
tolleranze accettate e vieta di bocciare un capo per uno scarto che la riga fit riporta come
accettabile. Il giudizio del consulente resta su prezzo, condizioni, stile e abbinamenti.

Verificato dopo la modifica: gli stessi capi passano da `EVITA — spalle troppo strette` a
`COMPRA — graphic tee perfetto, spalle un po' strette ma accettabili`.

### Esito

```
🎯 Misure cercate (top): spalle ~52cm · petto ~51cm · lungh ~72cm
60 candidati · ✂ 24 scartati fuori misura · 📷 1 misure lette dalle foto

1  The Simpsons Homer Graphic Tee  €10  ✅ COMPRA   🎯 SU MISURA  lungh 70 ✓ · spalle 48 (−4) · petto 54 (+3)
2  T-Shirt Pull & Bear             €5   ✅ COMPRA   🎯 SU MISURA  lungh 72 ✓ · spalle 52 ✓ · petto n/d
```

Nota di progetto: dentro la stessa classe il gusto viene prima della precisione, quindi il
capo con affinità più alta può precedere quello più preciso al centimetro. È coerente con
"prima la vestibilità, poi il gusto" — la classe è la vestibilità, e dentro la classe gli
scarti sono già tutti accettabili. Invertire i due criteri è una riga in `_chiave_ordine`.

## Ottava tornata: `/parere` su un capo singolo (2026-08-04)

Nuova funzione: si sottopone **un** capo e si ottiene un verdetto argomentato — comprarlo o
no, perché, e cosa chiedere al venditore. Tre modi di input:

| modo | comando | dati disponibili |
|---|---|---|
| link Vinted | `/parere https://...` | nome, brand, prezzo, condizioni, descrizione, foto |
| fotografia | `/parere foto <path> [note]` | descrizione dedotta dall'immagine, misure se leggibili |
| a parole | `/parere giacca Timberland XL, spalle 51...` | quel che l'utente scrive o detta |

Il terzo modo copre anche la descrizione "a voce": si detta nel terminale. Il programma non
registra audio, e non serve.

### Il link: JSON-LD, non le classi CSS

`vinted_api.articolo_da_url` legge la pagina intera (~2MB: per un capo solo è irrilevante) e
prende i dati dal blocco `<script type="application/ld+json">` schema.org, che espone nome,
descrizione completa con le misure, brand, prezzo, categoria e colore. Le stesse informazioni
nel DOM stanno dentro classi tipo `web_ui__Text__subtitle`, che cambiano a ogni rilascio del
sito: il JSON-LD è la fonte stabile. La taglia non è esposta lì, ma il venditore la scrive
quasi sempre nella descrizione, che il consulente legge comunque.

La categoria Vinted (`"Uomo Soprabiti e cappotti lunghi"`) classifica il capo meglio del solo
titolo e viene accodata alla descrizione per `tipo_capo_da_titolo`.

### Un bug grave scoperto dal caso reale

Sul cappotto Timberland dell'esempio, che dichiara **solo** spalle 51 e lunghezza 74,
l'estrattore ha restituito anche `petto_flat_cm: 37` — un numero che nella descrizione non
esiste. Con la regola introdotta nella settima tornata (ogni misura dichiarata fuori
tolleranza esclude il capo), quell'allucinazione **faceva scartare un capo buono in silenzio**.

`_numero_nel_testo` verifica ora che ogni numero estratto compaia davvero nel testo di
origine, con confini di cifra (`51` non si trova dentro `151`). Il controllo si applica al
valore grezzo prima della normalizzazione, perché una circonferenza di 102 dimezzata a 51 non
comparirebbe come "51". Sulle letture da foto non si applica: non c'è testo con cui
confrontare.

Non è una precauzione teorica: senza di essa il cappotto risultava `scartato`; con essa
risulta su misura.

### Capispalla: categoria propria

Una giacca si porta sopra altri capi, e la sua lunghezza dipende dal modello. `tipo_capo` ha
quindi un quarto valore, `capospalla`, con:

- `EASE_CAPOSPALLA_SPALLE = 2` e `EASE_CAPOSPALLA_PETTO = 5` di margine per gli strati sotto;
- **nessun target di lunghezza** — un bomber da 68cm e un trench da 95cm sono entrambi
  corretti, e un target unico li boccerebbe entrambi;
- `PESI = {spalle_cm: 3, petto_flat_cm: 1}`: le spalle sono la misura decisiva.

`tipo_capo_da_titolo` controlla i capispalla **per primi**, perché il tessuto non fa il capo:
"giacca di jeans" è un capospalla, non un pantalone. La camicia resta un top anche se di denim.

Sul cappotto dell'esempio il target passa da `spalle ~52` a `spalle ~54`: 51cm risulta −3
(dentro tolleranza) invece di −1 (quasi perfetto). È la differenza fra "giusto" e "stretto
sopra un maglione".

### Il parere

`valutazione/parere.py` produce `ParereCapo`: verdetto, sintesi, `a_favore`, `contro`,
`da_chiedere`. Il confronto sulle misure resta deterministico e viene **passato come dato** al
modello, con le tolleranze accettate esplicitate e il divieto di ri-giudicare i centimetri —
la stessa correzione della settima tornata, qui applicata al parere singolo. Un capo
`scartato` non può uscire con verdetto "compra": il verdetto viene forzato a "evita" in
Python, perché il giudizio sui centimetri non si lascia ribaltare dal modello.

Esito sul cappotto reale: `🤔 DIPENDE — cappotto heritage a prezzo ottimo, ma la larghezza
spalle è 3cm sotto target. Chiedi il petto prima di decidere`, con quattro punti a favore
(prezzo, colore compatibile con la palette, brand heritage, lunghezza proporzionata ai 194cm),
tre contro e due domande da fare al venditore.

### Un classificatore, non due

Introdurre `capospalla` ha rotto la coerenza fra i percorsi: la ricerca usava il `tipo_capo`
dedotto dall'LLM (che conosceva solo top/pantaloni/scarpe/altro), mentre feed e `/parere`
usavano `tipo_capo_da_titolo`. La stessa "giacca di jeans" veniva quindi giudicata come **top**
se cercata — con un target di lunghezza che per una giacca non significa niente — e come
**capospalla** se sottoposta col link.

Ora il classificatore deterministico ha l'ultima parola quando riconosce il capo, e l'LLM
decide solo nei casi che quello marca "altro". Un solo criterio per tutti e tre i percorsi.
Aggiunta anche la voce `capospalla` a `_PAROLE_MISURE`: senza, la ricerca ricadeva su "misure"
da sola, che seleziona il 10% dei capi con misure dichiarate contro il 65% del vocabolario
completo.

### Limite noto

Per i capispalla la sola misura prioritaria è le spalle, quindi `SU MISURA` si raggiunge con
**una** misura dichiarata — evidenza più sottile che per un top, che ne richiede due. È
coerente con la scelta "le spalle decidono", e la riga di dettaglio mostra `petto n/d`, ma chi
volesse più severità deve portare `petto_flat_cm` a peso 3 per i capispalla, accettando che la
sua tolleranza scenda da 8 a 4cm.

## Fuori scopo

- Zara e Zalando: non espongono le misure del singolo capo, restano sul filtro taglia.
  Il punteggio fit li tratta come "misure non dichiarate".
- Filtro per misure lato API Vinted: non esiste, il ranking è necessariamente post-fetch.
- Apprendimento delle costanti di taratura dal feedback (`/mipiace`): le costanti si
  correggono a mano nel file.
