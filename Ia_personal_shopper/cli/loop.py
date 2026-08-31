"""Loop REPL principale della CLI."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from rich.prompt import Confirm, Prompt
from rich.status import Status

from Ia_personal_shopper import vinted_api
from Ia_personal_shopper.browser import vinted as vinted_browser
from Ia_personal_shopper.browser.agente_base import crea_agente_carrello
from Ia_personal_shopper.cli.display import (
    console,
    stampa_aiuto,
    stampa_banner,
    stampa_coda,
    stampa_guardaroba,
    stampa_parere,
    stampa_preferiti,
    stampa_profilo,
    stampa_proposte,
    stampa_report_fit,
    stampa_risultati,
    stampa_siti,
    stampa_target_fit,
)
from Ia_personal_shopper.config import SITI_SUPPORTATI
from Ia_personal_shopper.models import (
    ParametriRicerca,
    ProdottoArricchito,
    ProdottoRisultato,
)
from Ia_personal_shopper.profilo.gestore import (
    aggiorna_preferenze,
    aggiungi_capo,
    aggiungi_gusti,
    aggiungi_preferito,
    aggiungi_stile,
    carica_guardaroba,
    carica_preferiti,
    carica_profilo,
    rimuovi_capo,
    salva_profilo,
)
from Ia_personal_shopper.profilo.gusti import descrittore_stile, impara_da_feed
from Ia_personal_shopper.ricerca.aggregatore import estrai_budget
from Ia_personal_shopper.ricerca.coordinatore import cerca_su_tutti_i_siti
from Ia_personal_shopper.ricerca.interprete import (
    interpreta_ricerca,
    taglie_per_tipo,
    tipo_capo_da_titolo,
)
from Ia_personal_shopper.ricerca.proposte import genera_proposte
from Ia_personal_shopper.valutazione.consulente import valuta_prodotti
from Ia_personal_shopper.valutazione.fit import (
    descrivi_target,
    estrai_da_descrizioni,
    misure_da_immagini,
    misure_target,
    ordina_per_fit,
    seleziona,
    unisci_misure,
    valuta,
)
from Ia_personal_shopper.valutazione.parere import parere_su_capo
from Ia_personal_shopper.vision.analizzatore import (
    descrivi_immagine,
    estrai_capi_da_outfit,
    estrai_stile_da_immagine,
    leggi_immagine,
)

# ---------------------------------------------------------------------------
# Stato sessione
# ---------------------------------------------------------------------------

_ultima_ricerca: list[ProdottoArricchito] = []
_ultima_coda: list[ProdottoArricchito] = []
_ultima_query: str = ""


# ---------------------------------------------------------------------------
# Handler comandi
# ---------------------------------------------------------------------------

async def _cmd_ricerca(testo: str) -> None:
    global _ultima_ricerca, _ultima_coda, _ultima_query

    profilo = carica_profilo()
    budget = estrai_budget(testo)

    # Usa budget default del profilo se non specificato nella query
    if budget is None and profilo.budget_default:
        budget = profilo.budget_default
        console.print(f"[dim]Budget non specificato, uso il default del profilo: €{budget:.0f}[/dim]")

    with Status("[cyan]Interpreto la richiesta...[/cyan]", console=console):
        params = await interpreta_ricerca(testo, profilo)

    # Mostra come è stata interpretata la richiesta
    dettagli = [f"cerco '[bold]{params.query}[/bold]'"]
    if params.colori:
        dettagli.append(f"colore: {', '.join(params.colori)}")
    taglie = taglie_per_tipo(params.tipo_capo, profilo)
    if taglie:
        dettagli.append(f"taglia {params.tipo_capo}: {taglie[0]}")
    if params.vestibilita != "regular":
        dettagli.append(params.vestibilita)
    if params.lunghezza != "regular":
        dettagli.append(f"lunghezza {params.lunghezza}")
    if params.genere:
        dettagli.append(params.genere)
    if budget:
        dettagli.append(f"max €{budget:.0f}")
    console.print(f"[dim]🧠 {' · '.join(dettagli)}[/dim]")
    if params.termini_stile:
        elenco = ", ".join(f"'[bold]{t}[/bold]'" for t in params.termini_stile)
        console.print(
            f"[magenta]🎨 Metà della ricerca insegue il tuo stile,[/magenta] "
            f"una passata per ciascuno: {elenco}"
        )

    # Le misure target rese esplicite prima di cercare: sono il criterio di selezione.
    target = misure_target(profilo, params.tipo_capo, params.vestibilita, params.lunghezza)
    stampa_target_fit(target, params.tipo_capo)

    siti_str = ", ".join(profilo.siti_attivi)
    console.print(f"\n[cyan]🔍 Cerco su {siti_str}...[/cyan]")

    with Status("[cyan]Navigazione in corso...[/cyan]", console=console):
        prodotti_raw = await cerca_su_tutti_i_siti(params, budget, profilo)

    if not prodotti_raw:
        console.print("[yellow]Nessun risultato. Prova con parole diverse o amplia il budget.[/yellow]")
        return

    with Status("[cyan]Leggo le misure dei capi...[/cyan]", console=console):
        prodotti_arricchiti, coda, report = await seleziona(prodotti_raw, profilo, params)

    stampa_report_fit(report)

    if not prodotti_arricchiti:
        if coda:
            console.print(
                "[yellow]Nessuno con misure dichiarate, ma ho messo in coda "
                f"{len(coda)} capi senza misure.[/yellow] Usa [bold]/coda[/bold] per vederli."
            )
        else:
            console.print(
                "[yellow]Tutti i candidati sono fuori misura.[/yellow] Prova ad allargare la "
                "richiesta, oppure alza [bold]SCARTO_MAX_CM[/bold] in valutazione/fit.py."
            )
        _ultima_ricerca = []
        _ultima_coda = coda
        _ultima_query = testo
        return

    console.print(f"[dim]💭 Elaboro le valutazioni per {len(prodotti_arricchiti)} prodotti...[/dim]")
    guardaroba = carica_guardaroba().capi
    prodotti_arricchiti = await valuta_prodotti(
        prodotti_arricchiti, profilo, budget, guardaroba, target
    )

    _ultima_ricerca = prodotti_arricchiti
    _ultima_coda = coda
    _ultima_query = testo

    stampa_risultati(prodotti_arricchiti, testo)

    if coda:
        console.print(
            f"\n[dim]🕗 {len(coda)} capi in coda senza misure. Usa [bold]/coda[/bold] per vederli.[/dim]"
        )


async def _cmd_login() -> None:
    """Apre la finestra dove l'utente accede a Vinted da sé (serve al feed personalizzato)."""
    if await asyncio.to_thread(vinted_api.sessione_autenticata):
        console.print("[green]✅ Sei già autenticato su Vinted.[/green] Usa [bold]/feed[/bold].")
        return

    console.print(
        "[cyan]Provo prima a usare la sessione Vinted del tuo Chrome.[/cyan]\n"
        "[dim]macOS chiederà il permesso di leggere la chiave 'Chrome Safe Storage': va "
        "approvato. Vengono letti solo i due token di sessione di vinted.it, nient'altro "
        "del tuo Chrome, e nessuna password.[/dim]"
    )
    await asyncio.to_thread(vinted_api._get_session, True)
    if await asyncio.to_thread(vinted_api.sessione_autenticata):
        console.print(
            "[green]✅ Uso la sessione già aperta nel tuo Chrome.[/green] "
            "Nessun accesso da rifare. Ora [bold]/feed[/bold] usa i tuoi consigli."
        )
        return

    console.print(
        "\n[yellow]Nel Chrome non ho trovato una sessione Vinted valida.[/yellow] "
        "Apro una finestra del browser come ripiego.\n"
        "[dim]Accedi tu con le tue credenziali: il programma non le chiede, non le legge e "
        "non le salva. Legge soltanto i cookie di sessione che il browser scrive su disco.[/dim]"
    )
    # In un thread: l'API sincrona di Playwright non può girare dentro l'event loop asyncio.
    if not await asyncio.to_thread(vinted_browser.apri_login):
        console.print(
            "[red]Non sono riuscito ad aprire la finestra.[/red] "
            "Verifica che Playwright sia installato: [bold]uv run playwright install chromium[/bold]"
        )
        return

    # La sessione in memoria ha ancora i cookie anonimi: va ricreata per rileggere il profilo.
    await asyncio.to_thread(vinted_api._get_session, True)
    if await asyncio.to_thread(vinted_api.sessione_autenticata):
        console.print("[green]✅ Accesso riuscito.[/green] Ora [bold]/feed[/bold] usa i tuoi consigli.")
    else:
        console.print(
            "[yellow]Non vedo una sessione autenticata.[/yellow] Se hai completato l'accesso, "
            "riprova con [bold]/login[/bold]; il feed nel frattempo resta quello generico."
        )


async def _cmd_feed() -> None:
    """Filtra i capi che Vinted ti consiglia, tenendo quelli che ti stanno davvero.

    Il gusto lo scegli tu navigando Vinted (il recommender impara dai capi che guardi e
    salvi); al programma resta il confronto sulle misure.
    """
    global _ultima_ricerca, _ultima_query

    profilo = carica_profilo()
    autenticato = await asyncio.to_thread(vinted_api.sessione_autenticata)
    if not autenticato:
        console.print(
            "[yellow]⚠ Sessione anonima:[/yellow] il feed di Vinted non è personalizzato "
            "(mostra capi generici, non i tuoi consigli). Usa [bold]/login[/bold] per "
            "collegare il tuo account.\n"
            "[dim]Procedo comunque sul feed generico, senza imparare nulla dai tuoi gusti.[/dim]"
        )

    with Status("[cyan]Leggo i capi che Vinted ti consiglia...[/cyan]", console=console):
        capi = await asyncio.to_thread(vinted_api.leggi_feed)

    if not capi:
        console.print("[yellow]Nessun capo nel feed. Riprova più tardi.[/yellow]")
        return
    console.print(f"[dim]📥 {len(capi)} capi consigliati da Vinted[/dim]")

    # Il feed è misto: le misure target dipendono dal tipo di capo, quindi si raggruppa e si
    # valuta un gruppo per volta. Quel che non è abbigliamento (il feed serve anche giocattoli,
    # carte Pokémon, borse) esce subito: qui si cercano capi che vestano.
    per_tipo: dict[str, list] = {}
    scartati_non_capi = 0
    fuori_taglia = 0
    for capo in capi:
        tipo = tipo_capo_da_titolo(capo.nome)
        if tipo == "altro":
            scartati_non_capi += 1
            continue
        # Stesso filtro morbido della ricerca: i capi senza taglia dichiarata passano.
        taglie = taglie_per_tipo(tipo, profilo)
        if taglie and not vinted_api.taglia_compatibile(capo.taglia_disponibile, taglie):
            fuori_taglia += 1
            continue
        per_tipo.setdefault(tipo, []).append(capo)

    righe = []
    if scartati_non_capi:
        righe.append(f"{scartati_non_capi} non abbigliamento")
    if fuori_taglia:
        righe.append(f"{fuori_taglia} fuori taglia")
    if righe:
        console.print(f"[dim]✂ scartati: {' · '.join(righe)}[/dim]")

    if not per_tipo:
        console.print(
            "[yellow]Nel feed non c'è nessun capo della tua taglia.[/yellow] "
            "Guarda qualche articolo su Vinted e riprova: il feed si adatta."
        )
        return

    arricchiti = []
    for tipo, gruppo in per_tipo.items():
        params = ParametriRicerca(
            query="", tipo_capo=tipo, genere=profilo.genere,
            vestibilita=profilo.vestibilita_preferita or "regular", lunghezza="regular",
        )
        target = misure_target(profilo, tipo, params.vestibilita, params.lunghezza)
        with Status(f"[cyan]Misuro i capi: {tipo} ({len(gruppo)})...[/cyan]", console=console):
            selezionati, _, report = await seleziona(gruppo, profilo, params)
        if report.attivo:
            console.print(
                f"[dim]  {tipo}: {report.candidati} → {len(selezionati)}"
                + (f" · ✂ {report.scartati} fuori misura" if report.scartati else "")
                + f" · 🎯 {descrivi_target(target)}[/dim]"
            )
        arricchiti += selezionati

    # I gruppi arrivano già ordinati al loro interno, ma l'ordine va rifatto sull'insieme.
    arricchiti = ordina_per_fit(arricchiti)

    if not arricchiti:
        console.print("[yellow]Nessuno dei capi consigliati ti sta bene.[/yellow]")
        return

    guardaroba = carica_guardaroba().capi
    with Status("[cyan]Elaboro i pareri...[/cyan]", console=console):
        arricchiti = await valuta_prodotti(arricchiti, profilo, None, guardaroba)

    _ultima_ricerca = arricchiti
    _ultima_query = "feed Vinted"
    stampa_risultati(arricchiti, "i capi che Vinted ti consiglia")

    # Il feed anonimo non rappresenta i gusti dell'utente: impararci sopra scriverebbe
    # "barbie vintage" nel profilo. Si impara solo da un feed autenticato.
    if not autenticato:
        return
    with Status("[cyan]Imparo i tuoi gusti dal feed...[/cyan]", console=console):
        stili = await impara_da_feed(capi)
    if stili:
        aggiungi_gusti(positivi=stili)
        console.print(
            f"[green]🎨 Ho imparato dai tuoi consigli:[/green] {', '.join(stili)}\n"
            "[dim]Li userò anche nelle ricerche normali.[/dim]"
        )


async def _cmd_parere(argomenti: str) -> None:
    """/parere <url vinted> | /parere foto <path> [note] | /parere <descrizione a parole>

    Tre modi per far valutare un capo singolo. Il link è il più completo (prezzo, brand,
    condizioni e descrizione col metro arrivano dalla pagina); la foto e il testo libero
    servono per un capo visto in negozio o descritto a voce dettando nel terminale.
    """
    argomenti = argomenti.strip()
    if not argomenti:
        console.print(
            "[red]Serve qualcosa da valutare.[/red]\n"
            "  [cyan]/parere https://www.vinted.it/items/...[/cyan]\n"
            "  [cyan]/parere foto /percorso/giacca.jpg[/cyan]\n"
            "  [cyan]/parere giacca Timberland XL, spalle 51, lunghezza 74, 30€[/cyan]"
        )
        return

    profilo = carica_profilo()
    prodotto: ProdottoRisultato | None = None
    immagini_locali: list[tuple[str, str]] = []

    # --- Modo 1: link Vinted ---
    if argomenti.startswith("http"):
        with Status("[cyan]Leggo l'annuncio...[/cyan]", console=console):
            prodotto = await asyncio.to_thread(vinted_api.articolo_da_url, argomenti)
        if prodotto is None:
            console.print(
                "[red]Non riesco a leggere quell'annuncio.[/red] "
                "Controlla il link, oppure descrivimi il capo a parole con le misure."
            )
            return
        testo_tipo = vinted_api.categoria_da_url_html(prodotto)

    # --- Modo 2: foto locale ---
    elif argomenti.startswith("foto "):
        resto = argomenti[len("foto "):].strip()
        percorso, _, note = resto.partition(" ")
        try:
            with Status("[cyan]Guardo la foto...[/cyan]", console=console):
                descrizione = await descrivi_immagine(percorso)
                dati, tipo = leggi_immagine(Path(percorso))
        except FileNotFoundError as e:
            console.print(f"[red]File non trovato: {e}[/red]")
            return
        except (ValueError, OSError) as e:
            console.print(f"[red]{e}[/red]")
            return
        immagini_locali = [(tipo, dati)]
        testo_note = note.strip()
        prodotto = ProdottoRisultato(
            nome=descrizione[:80],
            url=percorso,
            sito="foto",
            descrizione=f"{descrizione}\n{testo_note}".strip(),
        )
        testo_tipo = f"{descrizione} {testo_note}"

    # --- Modo 3: descrizione a parole ---
    else:
        prodotto = ProdottoRisultato(
            nome=argomenti[:80], url="", sito="descrizione", descrizione=argomenti,
        )
        testo_tipo = argomenti

    tipo_capo = tipo_capo_da_titolo(testo_tipo)
    target = misure_target(
        profilo, tipo_capo, profilo.vestibilita_preferita or "regular", "regular"
    )

    # Misure dal testo dell'annuncio (titolo + descrizione), validate contro il testo stesso.
    # Le foto delle inserzioni non si leggono più: le misure lette col metro in fotografia
    # erano inaffidabili e falsavano il giudizio. La vision resta solo per una foto tua,
    # dove non c'è nessun testo da leggere.
    misure = None
    with Status("[cyan]Cerco le misure...[/cyan]", console=console):
        dato = (await estrai_da_descrizioni([prodotto]) or {}).get(1)
        misure = dato[0] if isinstance(dato, tuple) else dato
        if immagini_locali:
            try:
                da_foto = await misure_da_immagini(immagini_locali)
            except Exception:
                da_foto = None
            if da_foto is not None:
                misure = unisci_misure(misure, da_foto)

    esito = valuta(misure, target, tipo_capo) if descrivi_target(target) else None

    guardaroba = carica_guardaroba().capi
    with Status("[cyan]Mi faccio un'opinione...[/cyan]", console=console):
        parere = await parere_su_capo(
            prodotto, profilo, tipo_capo, target, misure, esito, guardaroba
        )

    if parere is None:
        console.print("[red]Non sono riuscito a formulare un parere. Riprova.[/red]")
        return
    stampa_parere(prodotto, parere, esito, target)


async def _cmd_foto(argomenti: str) -> None:
    """Flow: /foto /percorso/immagine.jpg [budget N€] — la foto d'ispirazione guida una o più ricerche.

    Un outfit viene scomposto nei singoli capi: una ricerca personalizzata per ciascuno.
    """
    # Estrai path e budget opzionale
    match_budget = re.search(r"budget\s+(\d+(?:[.,]\d+)?)\s*€?", argomenti, re.IGNORECASE)
    budget_str = match_budget.group(0) if match_budget else ""
    path_immagine = argomenti.replace(budget_str, "").strip()

    if not path_immagine:
        console.print("[red]Specifica il percorso dell'immagine: /foto /percorso/foto.jpg[/red]")
        return

    console.print(f"[cyan]🖼  Analizzo l'immagine: {path_immagine}[/cyan]")

    try:
        with Status("[cyan]Analisi immagine in corso...[/cyan]", console=console):
            capi = await estrai_capi_da_outfit(path_immagine)
    except FileNotFoundError as e:
        console.print(f"[red]File non trovato: {e}[/red]")
        return
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return

    if not capi:
        console.print("[yellow]Non ho riconosciuto capi nell'immagine.[/yellow]")
        return

    console.print(f"[green]📝 Capi individuati:[/green] [italic]{' · '.join(capi)}[/italic]\n")

    # ponytail: outfit = ricerche sequenziali; _ultima_ricerca tiene l'ultimo capo (come /proponi).
    for i, capo in enumerate(capi, start=1):
        if len(capi) > 1:
            console.print(f"\n[bold magenta]— Capo {i}/{len(capi)}: {capo} —[/bold magenta]")
        query = f"{capo}, {budget_str}" if match_budget else capo
        await _cmd_ricerca(query)


def _numerati() -> list[ProdottoArricchito]:
    """Mostrati e coda in un unico spazio di indici, nell'ordine in cui appaiono a schermo.

    La coda continua la numerazione della tabella principale invece di ripartire da 1: con
    due numerazioni separate "/link 3" era ambiguo e risolveva sempre sui mostrati, quindi
    dei capi in coda non si riusciva a tirare fuori l'URL.
    """
    return _ultima_ricerca + _ultima_coda


def _arricchito_da_indice(argomenti: str) -> ProdottoArricchito | None:
    """Il capo N dell'ultima ricerca (coda inclusa), o None con messaggio d'errore."""
    tutti = _numerati()
    if not tutti:
        console.print("[yellow]Nessuna ricerca attiva. Esegui prima una ricerca.[/yellow]")
        return None
    try:
        n = int(argomenti.strip())
    except ValueError:
        console.print("[red]Specifica il numero del prodotto (es. 3)[/red]")
        return None
    if n < 1 or n > len(tutti):
        console.print(f"[red]Numero non valido. Scegli tra 1 e {len(tutti)}.[/red]")
        return None
    return tutti[n - 1]


def _prodotto_da_indice(argomenti: str) -> ProdottoRisultato | None:
    pa = _arricchito_da_indice(argomenti)
    return pa.prodotto if pa is not None else None


def _cmd_salva(argomenti: str) -> None:
    prodotto = _prodotto_da_indice(argomenti)
    if prodotto is None:
        return
    aggiungi_preferito(prodotto, _ultima_query)
    prezzo = f" (€{prodotto.prezzo:.0f})" if prodotto.prezzo else ""
    console.print(f"[green]✅ Salvato nei preferiti:[/green] {prodotto.nome}{prezzo}")


def _cmd_link(argomenti: str) -> None:
    """Stampa l'URL del prodotto N (per copiarlo o aprirlo dal terminale)."""
    prodotto = _prodotto_da_indice(argomenti)
    if prodotto is None:
        return
    console.print(f"[bold]{prodotto.nome}[/bold]\n[link={prodotto.url}]{prodotto.url}[/link]")


async def _cmd_feedback(argomenti: str, positivo: bool) -> None:
    """Registra un gusto appreso dal prodotto N.

    Il segnale è un descrittore di stile ("band tee nera anni 90"), non il brand: entra
    nelle varianti di gusto delle ricerche successive, mentre un nome di negozio no.
    """
    prodotto = _prodotto_da_indice(argomenti)
    if prodotto is None:
        return

    with Status("[cyan]Capisco cosa ti piace di questo capo...[/cyan]", console=console):
        segnale = await descrittore_stile(prodotto)
    if segnale is None:
        segnale = prodotto.brand or prodotto.nome   # fallback: meglio il brand che niente

    if positivo:
        aggiungi_gusti(positivi=[segnale])
        console.print(
            f"[green]👍 Registrato: ti piace [bold]{segnale}[/bold].[/green] "
            "[dim]Lo userò per cercare capi simili.[/dim]"
        )
    else:
        aggiungi_gusti(negativi=[segnale])
        console.print(f"[yellow]👎 Registrato: eviterò [bold]{segnale}[/bold] in futuro.[/yellow]")


async def _cmd_guardaroba(argomenti: str) -> None:
    """/guardaroba  |  /guardaroba aggiungi <desc>  |  /guardaroba foto <path>  |  /guardaroba rimuovi N"""
    argomenti = argomenti.strip()

    if not argomenti:
        stampa_guardaroba(carica_guardaroba().capi)
        return

    if argomenti.startswith("aggiungi "):
        descrizione = argomenti[len("aggiungi "):].strip()
        if not descrizione:
            console.print("[red]Uso: /guardaroba aggiungi <descrizione del capo>[/red]")
            return
        aggiungi_capo(descrizione)
        console.print(f"[green]✅ Aggiunto al guardaroba:[/green] {descrizione}")

    elif argomenti.startswith("foto "):
        path = argomenti[len("foto "):].strip()
        if not path:
            console.print("[red]Uso: /guardaroba foto /percorso/immagine.jpg[/red]")
            return
        try:
            with Status("[cyan]Analizzo il capo dalla foto...[/cyan]", console=console):
                descrizione = await descrivi_immagine(path)
        except FileNotFoundError as e:
            console.print(f"[red]File non trovato: {e}[/red]")
            return
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            return
        aggiungi_capo(descrizione)
        console.print(f"[green]✅ Aggiunto al guardaroba:[/green] [italic]{descrizione}[/italic]")

    elif argomenti.startswith("rimuovi "):
        capi = carica_guardaroba().capi
        try:
            n = int(argomenti[len("rimuovi "):].strip())
        except ValueError:
            console.print("[red]Specifica il numero del capo: /guardaroba rimuovi 3[/red]")
            return
        if n < 1 or n > len(capi):
            console.print(f"[red]Numero non valido. Scegli tra 1 e {len(capi)}.[/red]")
            return
        capo = capi[n - 1]
        rimuovi_capo(capo.id)
        console.print(f"[yellow]🗑  Rimosso dal guardaroba:[/yellow] {capo.descrizione}")

    else:
        console.print(
            "[red]Uso:[/red] [bold]/guardaroba[/bold] · "
            "[bold]/guardaroba aggiungi <desc>[/bold] · "
            "[bold]/guardaroba foto <path>[/bold] · "
            "[bold]/guardaroba rimuovi N[/bold]"
        )


async def _cmd_proponi() -> None:
    """Proposte proattive basate su profilo + guardaroba, poi ricerca del capo/outfit scelto."""
    profilo = carica_profilo()
    guardaroba = carica_guardaroba().capi

    with Status("[cyan]Penso a cosa potrebbe piacerti...[/cyan]", console=console):
        proposte = await genera_proposte(profilo, guardaroba)

    if not proposte:
        console.print("[yellow]Non sono riuscito a generare proposte. Riprova.[/yellow]")
        return

    stampa_proposte(proposte)

    scelta = Prompt.ask("Quale vuoi che cerchi? (numero, Invio per nessuna)", default="")
    scelta = scelta.strip()
    if not scelta:
        return
    try:
        n = int(scelta)
    except ValueError:
        console.print("[red]Inserisci un numero valido.[/red]")
        return
    if n < 1 or n > len(proposte):
        console.print(f"[red]Numero non valido. Scegli tra 1 e {len(proposte)}.[/red]")
        return

    proposta = proposte[n - 1]
    # ponytail: outfit = ricerche sequenziali; _ultima_ricerca tiene l'ultimo pezzo,
    # quindi /salva opera su quello. Accumulare se servirà salvare un intero outfit.
    for i, query in enumerate(proposta.ricerche, start=1):
        if len(proposta.ricerche) > 1:
            console.print(f"\n[bold magenta]— Pezzo {i}/{len(proposta.ricerche)}: {query} —[/bold magenta]")
        await _cmd_ricerca(query)


async def _cmd_stile(argomenti: str) -> None:
    """/stile foto <path>  |  /stile intervista"""
    argomenti = argomenti.strip()

    if argomenti.startswith("foto "):
        path = argomenti[len("foto "):].strip()
        if not path:
            console.print("[red]Uso: /stile foto /percorso/immagine.jpg[/red]")
            return
        try:
            with Status("[cyan]Analizzo lo stile dalla foto...[/cyan]", console=console):
                descrittori = await estrai_stile_da_immagine(path)
        except FileNotFoundError as e:
            console.print(f"[red]File non trovato: {e}[/red]")
            return
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            return
        aggiungi_stile(descrittori)
        console.print(f"[green]✅ Stile aggiornato con:[/green] {', '.join(descrittori)}")

    elif argomenti == "intervista":
        await _cmd_stile_intervista()

    else:
        console.print("[red]Uso: [bold]/stile foto <path>[/bold] oppure [bold]/stile intervista[/bold][/red]")


async def _cmd_stile_intervista() -> None:
    console.print("[cyan]Intervista di stile — rispondi liberamente, Invio per saltare una domanda.[/cyan]\n")
    domande = [
        "Che vestibilità preferisci? (aderente / regular / oversize)",
        "Quali colori ami indossare?",
        "Per quali occasioni compri più spesso? (lavoro, tempo libero, serate...)",
        "Quali brand o stili ti rappresentano?",
        "Cosa non indosseresti mai?",
    ]
    risposte = []
    for d in domande:
        r = Prompt.ask(f"  {d}", default="")
        if r.strip():
            risposte.append(f"{d} → {r.strip()}")

    if not risposte:
        console.print("[dim]Nessuna risposta, intervista annullata.[/dim]")
        return

    import anthropic
    from Ia_personal_shopper.config import MODELLO_VALUTAZIONE

    # Ogni risposta va nel proprio campo: gli stili finiscono nelle query di ricerca, i
    # colori e le occasioni solo nel giudizio. Impastarli — come faceva la versione
    # precedente — riempiva il vocabolario di ricerca di "nero" e "serate".
    prompt = (
        "Da queste risposte di un utente su gusti e stile, estrai tag brevi (1-3 parole, in "
        "italiano) smistandoli nei campi giusti. Rispondi SOLO con JSON:\n"
        '{"stili": [...], "colori": [...], "occasioni": [...], '
        '"vestibilita": "aderente"|"regular"|"oversize"|null, "da_evitare": [...]}\n\n'
        "- stili: solo estetiche e sottoculture (es. grunge, workwear, modern western). "
        "NON metterci colori, occasioni o vestibilità.\n"
        "- colori: solo nomi di colore.\n"
        "- occasioni: contesti d'uso (es. lavoro, serate, tempo libero).\n"
        "- vestibilita: come preferisce che i capi vestano, null se non emerge.\n"
        "- da_evitare: cose che l'utente non vuole.\n\nRisposte:\n" + "\n".join(risposte)
    )
    try:
        with Status("[cyan]Costruisco il tuo profilo di gusto...[/cyan]", console=console):
            client = anthropic.AsyncAnthropic()
            resp = await client.messages.create(
                model=MODELLO_VALUTAZIONE,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
        import json
        testo = resp.content[0].text
        dati = json.loads(testo[testo.find("{"):testo.rfind("}") + 1])
        stili = dati.get("stili") or []
        colori = dati.get("colori") or []
        occasioni = dati.get("occasioni") or []
        vestibilita = dati.get("vestibilita")
        da_evitare = dati.get("da_evitare") or []
    except Exception as e:
        console.print(f"[red]Errore nell'elaborazione: {e}[/red]")
        return

    aggiorna_preferenze(
        stili=stili,
        colori=colori,
        occasioni=occasioni,
        vestibilita=vestibilita,
        da_evitare=da_evitare,
    )
    console.print(f"[green]✅ Profilo aggiornato.[/green] Stili: {', '.join(stili) or '—'}")
    if colori:
        console.print(f"[dim]Colori: {', '.join(colori)}[/dim]")
    if occasioni:
        console.print(f"[dim]Occasioni: {', '.join(occasioni)}[/dim]")
    if vestibilita:
        console.print(f"[dim]Vestibilità preferita: {vestibilita}[/dim]")
    if da_evitare:
        console.print(f"[dim]Da evitare: {', '.join(da_evitare)}[/dim]")


async def _cmd_carrello(argomenti: str) -> None:
    pa = _arricchito_da_indice(argomenti)
    if pa is None:
        return
    p = pa.prodotto
    prezzo_str = f" (€{p.prezzo:.0f})" if p.prezzo else ""
    sito_str = p.sito.capitalize()

    console.print(
        f"\n[bold]Vuoi aggiungere al carrello su {sito_str}?[/bold]\n"
        f"  {p.nome}{prezzo_str}\n"
        f"  URL: [dim]{p.url}[/dim]"
    )

    confermato = Confirm.ask("Procedi?", default=False)
    if not confermato:
        console.print("[dim]Operazione annullata.[/dim]")
        return

    profilo = carica_profilo()

    # Vinted: aggiungi ai preferiti invece di fare offerte
    if p.sito == "vinted":
        console.print(
            "[yellow]ℹ️  Su Vinted le offerte sono irreversibili. "
            "Aggiungo l'articolo ai tuoi preferiti Vinted invece.[/yellow]"
        )
        task_str = vinted_browser.build_task_preferiti_vinted(p.url)
    else:
        from Ia_personal_shopper.browser import zalando as zalando_browser
        from Ia_personal_shopper.browser import zara as zara_browser
        taglia = profilo.taglie.top
        if p.sito == "zalando":
            task_str = zalando_browser.build_task_carrello(p.url, taglia)
        else:
            task_str = zara_browser.build_task_carrello(p.url, taglia)

    console.print(f"[cyan]🛒 Apro {sito_str}...[/cyan]")

    agente = crea_agente_carrello(p.url, task_str)
    await agente.run(max_steps=15)

    console.print(
        "[green]✅ Fatto! Il browser è rimasto aperto con la pagina del carrello. "
        "Puoi procedere manualmente al pagamento.[/green]"
    )


def _cmd_coda() -> None:
    """Mostra i capi senza misure dichiarate dalla ricerca più recente."""
    if not _ultima_coda:
        console.print("[dim]Nessun capo in coda. Esegui prima una ricerca.[/dim]")
        return
    # La numerazione riprende da dove finisce la tabella principale: un solo spazio di
    # indici per /link, /salva, /carrello e /mipiace (vedi _numerati).
    stampa_coda(_ultima_coda, _ultima_query, offset=len(_ultima_ricerca))


def _cmd_profilo_modifica() -> None:
    profilo = carica_profilo()
    console.print("[cyan]Modifica profilo — premi Invio per mantenere il valore attuale.[/cyan]\n")

    profilo.nome = Prompt.ask("Nome", default=profilo.nome)

    # Fisico
    console.print("\n[underline]Misure fisiche[/underline]")
    f = profilo.fisico

    def chiedi_int(label: str, val: int | None) -> int | None:
        default_str = str(val) if val else ""
        risposta = Prompt.ask(f"  {label} (cm)", default=default_str)
        return int(risposta) if risposta else None

    def chiedi_float(label: str, val: float | None) -> float | None:
        default_str = str(val) if val else ""
        risposta = Prompt.ask(f"  {label} (kg)", default=default_str)
        return float(risposta) if risposta else None

    f.altezza_cm = chiedi_int("Altezza", f.altezza_cm)
    f.peso_kg = chiedi_float("Peso", f.peso_kg)
    f.larghezza_spalle_cm = chiedi_int("Larghezza spalle", f.larghezza_spalle_cm)
    f.circonferenza_petto_cm = chiedi_int("Circonferenza petto", f.circonferenza_petto_cm)
    f.circonferenza_vita_cm = chiedi_int("Circonferenza vita", f.circonferenza_vita_cm)
    f.circonferenza_fianchi_cm = chiedi_int("Circonferenza fianchi", f.circonferenza_fianchi_cm)
    f.circonferenza_collo_cm = chiedi_int("Circonferenza collo", f.circonferenza_collo_cm)
    f.lunghezza_gamba_interna_cm = chiedi_int("Lunghezza gamba interna", f.lunghezza_gamba_interna_cm)
    nota = Prompt.ask("  Note corporatura", default=f.note_corporatura or "")
    f.note_corporatura = nota or None

    # Taglie
    console.print("\n[underline]Taglie abituali[/underline]")
    t = profilo.taglie
    t.top = Prompt.ask("  Taglia top/maglia (es. M, L, XL)", default=t.top or "") or None
    # La lunghezza va scritta: senza la L un W32 L30 passa come un W32 L36 (15cm di gamba).
    t.pantaloni = Prompt.ask(
        "  Taglia pantaloni (es. W32 L36)", default=t.pantaloni or ""
    ) or None
    t.scarpe = Prompt.ask("  Numero scarpe", default=t.scarpe or "") or None

    # Stile e budget
    console.print("\n[underline]Preferenze[/underline]")
    genere_str = Prompt.ask(
        "  Genere per il filtro ricerche (uomo/donna, Invio per nessun filtro)",
        default=profilo.genere or "",
    ).strip().lower()
    profilo.genere = genere_str if genere_str in ("uomo", "donna") else None

    # Campi separati: solo gli stili entrano nelle query di ricerca, quindi tenerci dentro
    # colori e occasioni degradava le ricerche.
    stili_str = Prompt.ask(
        "  Stili — estetiche e sottoculture, es. grunge, modern western (virgola)",
        default=", ".join(profilo.preferenze_stile),
    )
    profilo.preferenze_stile = [s.strip() for s in stili_str.split(",") if s.strip()]

    colori_str = Prompt.ask(
        "  Colori preferiti (virgola)", default=", ".join(profilo.colori_preferiti)
    )
    profilo.colori_preferiti = [c.strip() for c in colori_str.split(",") if c.strip()]

    occasioni_str = Prompt.ask(
        "  Occasioni d'uso, es. serate, lavoro (virgola)", default=", ".join(profilo.occasioni)
    )
    profilo.occasioni = [o.strip() for o in occasioni_str.split(",") if o.strip()]

    vest_str = Prompt.ask(
        "  Vestibilità preferita (aderente/regular/oversize, Invio per nessuna)",
        default=profilo.vestibilita_preferita or "",
    ).strip().lower()
    profilo.vestibilita_preferita = vest_str if vest_str in ("aderente", "regular", "oversize") else None

    budget_str = Prompt.ask("  Budget default (€)", default=str(profilo.budget_default))
    try:
        profilo.budget_default = float(budget_str)
    except ValueError:
        pass

    salva_profilo(profilo)
    console.print("\n[green]✅ Profilo aggiornato![/green]")
    stampa_profilo(profilo)


def _cmd_siti() -> None:
    profilo = carica_profilo()
    stampa_siti(profilo)

    sito_input = Prompt.ask(
        "Nome sito da attivare/disattivare (Invio per non cambiare nulla)",
        default="",
    )
    sito_input = sito_input.strip().lower()
    if not sito_input:
        return

    if sito_input not in SITI_SUPPORTATI:
        console.print(f"[red]Sito non supportato. Siti disponibili: {', '.join(SITI_SUPPORTATI)}[/red]")
        return

    if sito_input in profilo.siti_attivi:
        profilo.siti_attivi.remove(sito_input)
        console.print(f"[yellow]⬜ {sito_input.capitalize()} disattivato.[/yellow]")
    else:
        profilo.siti_attivi.append(sito_input)
        console.print(f"[green]✅ {sito_input.capitalize()} attivato.[/green]")

    salva_profilo(profilo)


# ---------------------------------------------------------------------------
# Loop principale
# ---------------------------------------------------------------------------

async def avvia() -> None:
    stampa_banner()

    loop = asyncio.get_running_loop()

    while True:
        try:
            testo = await loop.run_in_executor(
                None, lambda: console.input("[bold cyan]Tu:[/bold cyan] ").strip()
            )
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Arrivederci![/dim]")
            break

        if not testo:
            continue

        # --- Routing comandi ---
        if testo == "/esci" or testo == "/exit" or testo == "/quit":
            console.print("[dim]Arrivederci![/dim]")
            break

        elif testo == "/aiuto" or testo == "/help":
            stampa_aiuto()

        elif testo == "/profilo modifica":
            _cmd_profilo_modifica()

        elif testo == "/profilo":
            profilo = carica_profilo()
            stampa_profilo(profilo)

        elif testo == "/siti":
            _cmd_siti()

        elif testo == "/preferiti":
            lista = carica_preferiti()
            stampa_preferiti(lista.preferiti)

        elif testo == "/proponi":
            await _cmd_proponi()

        elif testo == "/login":
            await _cmd_login()

        elif testo == "/feed":
            await _cmd_feed()

        elif testo == "/parere" or testo.startswith("/parere "):
            await _cmd_parere(testo[len("/parere"):].strip())

        elif testo == "/guardaroba" or testo.startswith("/guardaroba "):
            await _cmd_guardaroba(testo[len("/guardaroba"):].strip())

        # startswith + strip: così "/salva" senza numero mostra il messaggio d'uso
        # dell'handler invece di "comando non riconosciuto".
        elif testo == "/link" or testo.startswith("/link "):
            _cmd_link(testo[len("/link"):].strip())

        elif testo == "/salva" or testo.startswith("/salva "):
            _cmd_salva(testo[len("/salva"):].strip())

        elif testo == "/carrello" or testo.startswith("/carrello "):
            await _cmd_carrello(testo[len("/carrello"):].strip())

        elif testo == "/coda":
            _cmd_coda()

        elif testo == "/foto" or testo.startswith("/foto "):
            await _cmd_foto(testo[len("/foto"):].strip())

        elif testo.startswith("/stile"):
            await _cmd_stile(testo[len("/stile"):])

        elif testo == "/mipiace" or testo.startswith("/mipiace "):
            await _cmd_feedback(testo[len("/mipiace"):].strip(), positivo=True)

        elif testo == "/nonmipiace" or testo.startswith("/nonmipiace "):
            await _cmd_feedback(testo[len("/nonmipiace"):].strip(), positivo=False)

        elif testo.startswith("/"):
            console.print(f"[red]Comando non riconosciuto: {testo}[/red] — usa [bold]/aiuto[/bold]")

        else:
            await _cmd_ricerca(testo)
