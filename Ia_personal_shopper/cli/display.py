"""Funzioni di rendering Rich per la CLI."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from Ia_personal_shopper.models import (
    ArticoloPreferito,
    CapoGuardaroba,
    EsitoFit,
    MisureTarget,
    ParereCapo,
    ProdottoArricchito,
    ProdottoRisultato,
    ProfiloUtente,
    Proposta,
    ReportFit,
)
from Ia_personal_shopper.valutazione.fit import (
    CLASSE_NON_DICHIARATE,
    descrivi_target,
    etichetta_fit,
)

console = Console()

BANNER = """[bold cyan]
 ╔═══════════════════════════════════════╗
 ║   🛍️  Personal AI Shopper             ║
 ║   Il tuo agente di moda personale     ║
 ╚═══════════════════════════════════════╝[/bold cyan]"""

COLORI_SITO = {
    "zalando": "orange3",
    "zara": "white",
    "vinted": "green3",
}

ICONE_SITO = {
    "zalando": "🟠",
    "zara": "⬛",
    "vinted": "🟢",
}

COLORI_RACCOMANDAZIONE = {
    "compra": "bold green",
    "considera": "bold yellow",
    "evita": "bold red",
}

ICONE_RACCOMANDAZIONE = {
    "compra": "✅",
    "considera": "🤔",
    "evita": "❌",
}


def stampa_banner() -> None:
    console.print(BANNER)
    console.print("[dim]Digita una ricerca, /aiuto per i comandi, /esci per uscire.[/dim]\n")


def stampa_target_fit(target: MisureTarget, tipo_capo: str) -> None:
    """Le misure cercate, rese esplicite prima della ricerca."""
    descrizione = descrivi_target(target)
    if descrizione:
        console.print(f"[cyan]🎯 Misure cercate ({tipo_capo}):[/cyan] [bold]{descrizione}[/bold]")


def stampa_report_fit(report: ReportFit) -> None:
    """Contabilità della selezione: quanti candidati, quanti scartati e perché."""
    if not report.attivo:
        return
    righe = [f"{report.candidati} candidati"]
    if report.fuori_taglia:
        righe.append(f"[yellow]✂ {report.fuori_taglia} fuori taglia[/yellow]")
    if report.scartati:
        dettaglio = f" (il più vicino: {report.miglior_scartato})" if report.miglior_scartato else ""
        righe.append(f"[yellow]✂ {report.scartati} scartati fuori misura{dettaglio}[/yellow]")
    if report.in_coda:
        righe.append(f"🕗 {report.in_coda} senza misure, in coda")
    console.print("[dim]" + " · ".join(righe) + "[/dim]")
    if report.errore_descrizioni:
        console.print(
            "[red]⚠ La lettura delle misure dalle descrizioni è fallita[/red] — i capi qui "
            "sotto risultano senza misure per un problema tecnico, non perché il venditore "
            "non le abbia scritte. Controlla ANTHROPIC_API_KEY e la connessione."
        )


_TITOLO_VERDETTO = {
    "compra": ("✅ COMPRALO", "bold green"),
    "considera": ("🤔 DIPENDE", "bold yellow"),
    "evita": ("❌ LASCIALO", "bold red"),
}


def stampa_parere(
    prodotto: ProdottoRisultato,
    parere: ParereCapo,
    fit: EsitoFit | None,
    target: MisureTarget,
) -> None:
    """Il parere su un singolo capo, con il confronto sulle misure in chiaro."""
    titolo, stile = _TITOLO_VERDETTO.get(parere.verdetto, ("PARERE", "bold"))

    righe = [f"[{stile}]{titolo}[/{stile}]", f"[italic]{parere.sintesi}[/italic]", ""]

    dati = []
    if prodotto.brand:
        dati.append(prodotto.brand)
    if prodotto.prezzo is not None:
        dati.append(f"€{prodotto.prezzo:.2f}")
    if prodotto.taglia_disponibile:
        dati.append(f"taglia {prodotto.taglia_disponibile}")
    if prodotto.colore:
        dati.append(prodotto.colore)
    if prodotto.fit_dichiarato:
        dati.append(prodotto.fit_dichiarato)
    if prodotto.condizione:
        dati.append(prodotto.condizione)
    if dati:
        righe.append("[dim]" + " · ".join(dati) + "[/dim]")

    descrizione_target = descrivi_target(target)
    if descrizione_target:
        righe.append(f"[cyan]🎯 Cercavamo:[/cyan] {descrizione_target}")
    if fit is not None:
        testo_fit, stile_fit = etichetta_fit(fit)
        if fit.classe == CLASSE_NON_DICHIARATE:
            righe.append(f"[{stile_fit}]{testo_fit}[/{stile_fit}]")
        else:
            righe.append(f"[{stile_fit}]{testo_fit}[/{stile_fit}]  [cyan]{fit.dettaglio}[/cyan]")
        if fit.scartato:
            righe.append(f"[red]✂ fuori misura: {fit.motivo_scarto}[/red]")

    for etichetta, voci, colore in (
        ("A favore", parere.a_favore, "green"),
        ("Contro", parere.contro, "red"),
        ("Da chiedere al venditore", parere.da_chiedere, "yellow"),
    ):
        if voci:
            righe.append("")
            righe.append(f"[bold {colore}]{etichetta}:[/bold {colore}]")
            righe += [f"  • {v}" for v in voci]

    console.print(Panel("\n".join(righe), title=prodotto.nome[:70], border_style=stile.split()[-1]))
    if prodotto.url and prodotto.url.startswith("http"):
        console.print(f"[dim][link={prodotto.url}]{prodotto.url}[/link][/dim]")


def _cella_prodotto(p: ProdottoRisultato) -> Text:
    """Nome (link cliccabile), brand, colore e vestibilità dichiarati, condizione.

    Costruita con Text e non con la sintassi markup: i titoli dei venditori contengono
    parentesi quadre ("[NUOVO]"), che from_markup interpreterebbe come tag di stile.
    """
    nome = p.nome[:60] + "…" if len(p.nome) > 60 else p.nome
    # Il link è un OSC 8: i terminali che non lo supportano mostrano il nome e basta,
    # per questo l'URL viene comunque stampato in chiaro sotto la tabella (_stampa_link).
    cella = Text(nome, style=f"link {p.url}" if p.url.startswith("http") else "")
    if p.brand:
        cella.append(f"\n{p.brand}", style="dim")
    # Colore e vestibilità come li ha scritti il venditore (vedi valutazione/fit.py):
    # sono le due cose che il titolo di un annuncio Vinted quasi sempre tace.
    dettagli = " · ".join(d for d in (p.colore, p.fit_dichiarato) if d)
    if dettagli:
        cella.append(f"\n{dettagli}", style="cyan")
    if p.condizione:
        cella.append(f"\n{p.condizione}", style="dim italic")
    return cella


def _stampa_link(prodotti: list[ProdottoArricchito], offset: int = 0) -> None:
    """Gli URL numerati sotto la tabella, uno per riga.

    Il nome in tabella è già un hyperlink, ma metà dei terminali (Terminal.app fra questi)
    ignora gli OSC 8 e lo mostra come testo morto. Qui l'URL è visibile e cliccabile
    ovunque, e nessuno deve più chiedere /link N.
    """
    for i, pa in enumerate(prodotti, start=offset + 1):
        # soft_wrap: senza, Rich spezza l'URL a metà con un a capo vero, e da un link
        # tagliato in due righe non ci si clicca più.
        console.print(
            f"  [dim]{i}.[/dim] [link={pa.prodotto.url}]{pa.prodotto.url}[/link]",
            soft_wrap=True,
        )


def _tabella_capi(titolo: str) -> Table:
    table = Table(
        title=titolo,
        show_header=True,
        header_style="bold cyan",
        show_lines=True,
        expand=True,
    )
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Sito", width=9)
    table.add_column("Prodotto", min_width=25)
    table.add_column("Prezzo", width=8, justify="right")
    # I jeans dichiarano "W32 L34" o "W32 | IT 46": con 7 caratteri la colonna andava a capo.
    table.add_column("Taglia", width=12, justify="center")
    table.add_column("Parere", min_width=30)
    return table


def _aggiungi_capo(table: Table, numero: int, pa: ProdottoArricchito) -> None:
    p, v = pa.prodotto, pa.valutazione

    icona = ICONE_SITO.get(p.sito, "🔵")
    colore_sito = COLORI_SITO.get(p.sito, "white")
    sito_text = Text(f"{icona} {p.sito.capitalize()}", style=colore_sito)

    prodotto_text = _cella_prodotto(p)

    prezzo_str = f"€{p.prezzo:.0f}" if p.prezzo is not None else "[dim]N/D[/dim]"

    # Giudizio del consulente + riga misure calcolata da fit.py
    righe_parere = []
    if v:
        colore = COLORI_RACCOMANDAZIONE.get(v.raccomandazione, "white")
        icona_racc = ICONE_RACCOMANDAZIONE.get(v.raccomandazione, "•")
        righe_parere.append(f"[{colore}]{icona_racc} {v.raccomandazione.upper()}[/{colore}]")
        righe_parere.append(f"[italic]{v.commento}[/italic]")

    if pa.fit is not None:
        testo_fit, stile_fit = etichetta_fit(pa.fit)
        if pa.fit.classe == CLASSE_NON_DICHIARATE:
            righe_parere.append(f"[{stile_fit}]{testo_fit}[/{stile_fit}]")
        else:
            righe_parere.append(
                f"[{stile_fit}]{testo_fit}[/{stile_fit}]  [cyan]{pa.fit.dettaglio}[/cyan]"
            )

    table.add_row(
        str(numero),
        sito_text,
        prodotto_text,
        prezzo_str,
        p.taglia_disponibile or "[dim]-[/dim]",
        Text.from_markup("\n".join(righe_parere) if righe_parere else "[dim]—[/dim]"),
    )


def stampa_risultati(prodotti: list[ProdottoArricchito], query: str) -> None:
    if not prodotti:
        console.print("[yellow]Nessun risultato trovato. Prova con una ricerca diversa.[/yellow]")
        return

    table = _tabella_capi(f"Risultati per: [bold]{query}[/bold]")
    for i, pa in enumerate(prodotti, start=1):
        _aggiungi_capo(table, i, pa)

    console.print(table)
    _stampa_link(prodotti)
    console.print(
        "\n[dim]Comandi: [bold]/salva N[/bold] salva preferito · "
        "[bold]/carrello N[/bold] aggiunge al carrello · "
        "[bold]/coda[/bold] mostra i capi senza misure · "
        "[bold]/aiuto[/bold] tutti i comandi[/dim]"
    )


def stampa_coda(prodotti: list[ProdottoArricchito], query: str, offset: int = 0) -> None:
    """I capi senza misure dichiarate, ordinati per gusto.

    `offset` = quanti capi ci sono nella tabella principale: la numerazione riprende da lì
    invece di ripartire da 1, così /link N e /salva N valgono su un unico spazio di indici.
    """
    if not prodotti:
        console.print("[dim]Nessun capo in coda.[/dim]")
        return

    table = _tabella_capi(f"🕗 In coda — senza misure dichiarate: [bold]{query}[/bold]")
    for i, pa in enumerate(prodotti, start=offset + 1):
        _aggiungi_capo(table, i, pa)

    console.print(table)
    console.print(
        "[dim]Il venditore non ha scritto le misure: questi capi potrebbero piacerti, ma la "
        "vestibilità è da verificare — chiedila prima di comprare.[/dim]\n"
    )
    _stampa_link(prodotti, offset)
    console.print(
        f"\n[dim]Numerati da [bold]{offset + 1}[/bold] a [bold]{offset + len(prodotti)}[/bold], "
        f"di seguito alla tabella principale: [bold]/salva N[/bold], "
        f"[bold]/carrello N[/bold] e [bold]/mipiace N[/bold] funzionano anche qui.[/dim]"
    )


def stampa_profilo(profilo: ProfiloUtente) -> None:
    fisico = profilo.fisico
    taglie = profilo.taglie

    righe_fisico = []
    if fisico.altezza_cm:
        righe_fisico.append(f"Altezza: [bold]{fisico.altezza_cm}cm[/bold]")
    if fisico.peso_kg:
        righe_fisico.append(f"Peso: [bold]{fisico.peso_kg}kg[/bold]")
    if fisico.larghezza_spalle_cm:
        righe_fisico.append(f"Spalle: [bold]{fisico.larghezza_spalle_cm}cm[/bold]")
    if fisico.circonferenza_petto_cm:
        righe_fisico.append(f"Petto: [bold]{fisico.circonferenza_petto_cm}cm[/bold]")
    if fisico.circonferenza_vita_cm:
        righe_fisico.append(f"Vita: [bold]{fisico.circonferenza_vita_cm}cm[/bold]")
    if fisico.circonferenza_fianchi_cm:
        righe_fisico.append(f"Fianchi: [bold]{fisico.circonferenza_fianchi_cm}cm[/bold]")
    if fisico.circonferenza_collo_cm:
        righe_fisico.append(f"Collo: [bold]{fisico.circonferenza_collo_cm}cm[/bold]")
    if fisico.lunghezza_gamba_interna_cm:
        righe_fisico.append(f"Gamba interna: [bold]{fisico.lunghezza_gamba_interna_cm}cm[/bold]")
    if fisico.note_corporatura:
        righe_fisico.append(f"Note: [italic]{fisico.note_corporatura}[/italic]")

    righe_taglie = []
    if taglie.top:
        righe_taglie.append(f"Top/maglia: [bold]{taglie.top}[/bold]")
    if taglie.pantaloni:
        righe_taglie.append(f"Pantaloni: [bold]{taglie.pantaloni}[/bold]")
    if taglie.scarpe:
        righe_taglie.append(f"Scarpe: [bold]{taglie.scarpe}[/bold]")

    stili = ", ".join(profilo.preferenze_stile) if profilo.preferenze_stile else "non specificato"
    colori_pref = ", ".join(profilo.colori_preferiti) if profilo.colori_preferiti else "—"
    occasioni = ", ".join(profilo.occasioni) if profilo.occasioni else "—"
    siti = ", ".join(profilo.siti_attivi) if profilo.siti_attivi else "nessuno"
    gusti_pos = ", ".join(profilo.gusti_positivi) if profilo.gusti_positivi else "—"
    gusti_neg = ", ".join(profilo.gusti_negativi) if profilo.gusti_negativi else "—"

    contenuto = (
        f"[bold cyan]👤 {profilo.nome}[/bold cyan]\n\n"
        f"[underline]Misure fisiche:[/underline]\n"
        + ("\n".join(righe_fisico) if righe_fisico else "[dim]Non inserite[/dim]")
        + "\n\n[underline]Taglie abituali:[/underline]\n"
        + ("\n".join(righe_taglie) if righe_taglie else "[dim]Non inserite[/dim]")
        + f"\n\n[underline]Stili:[/underline] {stili} [dim](entrano nelle ricerche)[/dim]"
        f"\n[underline]Colori preferiti:[/underline] {colori_pref}"
        f"\n[underline]Occasioni:[/underline] {occasioni}"
        f"\n[underline]Vestibilità preferita:[/underline] "
        f"{profilo.vestibilita_preferita or 'non specificata'}"
        f"\n[underline]Gusti — piacciono:[/underline] {gusti_pos}"
        f"\n[underline]Gusti — da evitare:[/underline] {gusti_neg}"
        f"\n[underline]Genere:[/underline] {profilo.genere or 'nessun filtro'}"
        f"\n[underline]Budget default:[/underline] €{profilo.budget_default:.0f}"
        f"\n[underline]Siti attivi:[/underline] {siti}"
    )

    console.print(Panel(contenuto, title="Il tuo profilo", border_style="cyan"))
    console.print("[dim]Usa [bold]/profilo modifica[/bold] per aggiornare le tue informazioni.[/dim]")


def stampa_preferiti(preferiti: list[ArticoloPreferito]) -> None:
    if not preferiti:
        console.print("[dim]Nessun articolo nei preferiti.[/dim]")
        return

    table = Table(
        title="❤️  I tuoi preferiti",
        show_header=True,
        header_style="bold cyan",
        show_lines=True,
        expand=True,
    )
    table.add_column("ID", style="dim", width=6)
    table.add_column("Data", width=11)
    table.add_column("Sito", width=9)
    table.add_column("Prodotto", min_width=25)
    table.add_column("Prezzo", width=8, justify="right")
    table.add_column("Ricerca originale", min_width=20)

    for art in preferiti:
        p = art.prodotto
        data = art.salvato_il[:10] if art.salvato_il else "—"
        icona = ICONE_SITO.get(p.sito, "🔵")
        prezzo_str = f"€{p.prezzo:.0f}" if p.prezzo is not None else "N/D"

        table.add_row(
            art.id[:6],
            data,
            f"{icona} {p.sito.capitalize()}",
            _cella_prodotto(p),
            prezzo_str,
            art.query_originale[:30],
        )

    console.print(table)


def stampa_guardaroba(capi: list[CapoGuardaroba]) -> None:
    if not capi:
        console.print(
            "[dim]Guardaroba vuoto. Aggiungi capi con "
            "[bold]/guardaroba aggiungi <desc>[/bold] o [bold]/guardaroba foto <path>[/bold].[/dim]"
        )
        return

    table = Table(
        title="👔 Il tuo guardaroba",
        show_header=True,
        header_style="bold cyan",
        show_lines=True,
        expand=True,
    )
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Data", width=11)
    table.add_column("Capo", min_width=30)

    for i, c in enumerate(capi, start=1):
        data = c.aggiunto_il[:10] if c.aggiunto_il else "—"
        table.add_row(str(i), data, c.descrizione)

    console.print(table)
    console.print("[dim]Rimuovi un capo con [bold]/guardaroba rimuovi N[/bold].[/dim]")


def stampa_proposte(proposte: list[Proposta]) -> None:
    console.print("\n[bold cyan]💡 Ho pensato a queste proposte per te:[/bold cyan]\n")
    for i, p in enumerate(proposte, start=1):
        tipo = "outfit" if len(p.ricerche) > 1 else "capo"
        console.print(f"[bold]{i}. {p.titolo}[/bold] [dim]({tipo})[/dim]")
        console.print(f"   [italic]{p.motivo}[/italic]")
        for q in p.ricerche:
            console.print(f"   [dim]→ {q}[/dim]")
        console.print()


def stampa_aiuto() -> None:
    contenuto = (
        "[bold]Ricerca:[/bold]\n"
        "  Scrivi liberamente es: [cyan]Cerca una giacca di pelle marrone, max 80€[/cyan]\n"
        "  Da foto d'ispirazione (capo o outfit): [cyan]/foto /percorso/immagine.jpg budget 60€[/cyan]\n"
        "  [dim]Un outfit viene scomposto nei singoli capi, uno per ricerca.[/dim]\n\n"
        "[bold]Parere su un capo singolo:[/bold]\n"
        "  [cyan]/parere <link vinted>[/cyan]      → Legge annuncio, misure e foto, e dice se comprarlo\n"
        "  [cyan]/parere foto <path> [note][/cyan] → Valuta un capo da una tua fotografia\n"
        "  [cyan]/parere <descrizione>[/cyan]      → Descrivilo a parole con le misure che sai\n"
        "  [dim]es: /parere giacca Timberland XL, spalle 51, lunghezza 74, 30€[/dim]\n\n"
        "[bold]Feed Vinted (gusto scelto da Vinted, misure controllate dal programma):[/bold]\n"
        "  [cyan]/login[/cyan]            → Collega il tuo account: accedi tu nella finestra\n"
        "  [cyan]/feed[/cyan]             → Filtra i capi che Vinted ti consiglia, tenendo\n"
        "                       solo quelli che ti stanno, e impara i tuoi gusti\n\n"
        "[bold]Proposte proattive:[/bold]\n"
        "  [cyan]/proponi[/cyan]          → L'IA propone capi e outfit su misura, poi li cerca\n\n"
        "[bold]Guardaroba:[/bold]\n"
        "  [cyan]/guardaroba[/cyan]                  → Mostra i capi che possiedi\n"
        "  [cyan]/guardaroba aggiungi <desc>[/cyan] → Aggiungi un capo a parole\n"
        "  [cyan]/guardaroba foto <path>[/cyan]     → Aggiungi un capo da foto\n"
        "  [cyan]/guardaroba rimuovi N[/cyan]       → Rimuovi il capo N\n\n"
        "[bold]Stile e gusti:[/bold]\n"
        "  [cyan]/stile foto <path>[/cyan] → Deduci lo stile da una foto di ispirazione\n"
        "  [cyan]/stile intervista[/cyan]  → Questionario per costruire i tuoi gusti\n"
        "  [cyan]/mipiace N[/cyan]         → Segnala che il prodotto N ti piace (impara)\n"
        "  [cyan]/nonmipiace N[/cyan]      → Segnala che il prodotto N non ti piace\n\n"
        "[bold]Comandi:[/bold]\n"
        "  [cyan]/profilo[/cyan]          → Visualizza il tuo profilo\n"
        "  [cyan]/profilo modifica[/cyan] → Modifica misure e preferenze\n"
        "  [cyan]/siti[/cyan]             → Gestisci i siti di ricerca\n"
        "  [cyan]/preferiti[/cyan]        → Articoli salvati\n"
        "  [cyan]/link N[/cyan]           → Mostra l'URL del prodotto N\n"
        "  [cyan]/salva N[/cyan]          → Salva il prodotto N tra i preferiti\n"
        "  [cyan]/carrello N[/cyan]       → Aggiungi il prodotto N al carrello\n"
        "  [cyan]/coda[/cyan]             → Mostra i capi senza misure (ordinati per gusto)\n"
        "  [cyan]/aiuto[/cyan]            → Questo messaggio\n"
        "  [cyan]/esci[/cyan]             → Esci dall'applicazione\n"
    )
    console.print(Panel(contenuto, title="Aiuto", border_style="cyan"))


def stampa_siti(profilo: ProfiloUtente) -> None:
    from Ia_personal_shopper.config import SITI_SUPPORTATI
    righe = []
    for sito in SITI_SUPPORTATI:
        attivo = sito in profilo.siti_attivi
        stato = "[green]✓ attivo[/green]" if attivo else "[red]✗ disattivo[/red]"
        icona = ICONE_SITO.get(sito, "🔵")
        righe.append(f"  {icona} [bold]{sito.capitalize()}[/bold] — {stato}")
    contenuto = "\n".join(righe) + "\n\n[dim]Digita il nome del sito per attivarlo/disattivarlo.[/dim]"
    console.print(Panel(contenuto, title="Siti di ricerca", border_style="cyan"))


if __name__ == "__main__":
    # Self-check offline del rendering: la cella prodotto è l'unico punto dove il testo
    # scritto dai venditori finisce dentro Rich, ed è dove si rompeva.
    from Ia_personal_shopper.models import ProdottoRisultato as _P

    capo = _P(
        nome="T-shirt Nirvana [NUOVO] 100% cotone",   # le quadre erano markup per Rich
        brand="Nirvana",
        url="https://www.vinted.it/items/123-nirvana",
        sito="vinted",
        colore="nero",
        fit_dichiarato="oversize",
        condizione="ottime condizioni",
    )
    cella = _cella_prodotto(capo)
    testo = cella.plain
    assert "[NUOVO]" in testo, testo          # non interpretato come tag di stile
    assert "nero · oversize" in testo, testo  # colore e fit dichiarati, sotto il brand
    assert cella.style == f"link {capo.url}", cella.style   # il nome È il link

    # Un capo senza URL http (foto locale, descrizione a voce) non deve produrre un link rotto
    senza_link = _cella_prodotto(_P(nome="giacca vista in negozio", url="", sito="foto"))
    assert senza_link.plain == "giacca vista in negozio"

    # L'URL in chiaro sotto la tabella: è quello che rende cliccabile anche Terminal.app
    console.print(cella)
    _stampa_link([ProdottoArricchito(prodotto=capo)])
    print("OK")
