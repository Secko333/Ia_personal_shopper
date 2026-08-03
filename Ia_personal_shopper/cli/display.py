"""Funzioni di rendering Rich per la CLI."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from Ia_personal_shopper.models import (
    ArticoloPreferito,
    CapoGuardaroba,
    MisureTarget,
    ProdottoArricchito,
    ProfiloUtente,
    Proposta,
    ReportFit,
)
from Ia_personal_shopper.valutazione.fit import descrivi_target, etichetta_fit

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
    if report.scartati:
        dettaglio = f" (il più vicino: {report.miglior_scartato})" if report.miglior_scartato else ""
        righe.append(f"[yellow]✂ {report.scartati} scartati fuori misura{dettaglio}[/yellow]")
    if report.letti_da_foto:
        righe.append(f"📷 {report.letti_da_foto} misure lette dalle foto")
    if report.errori_foto:
        righe.append(f"[yellow]⚠ {report.errori_foto} letture da foto non riuscite[/yellow]")
    console.print("[dim]" + " · ".join(righe) + "[/dim]")
    if report.errore_descrizioni:
        console.print(
            "[red]⚠ La lettura delle misure dalle descrizioni è fallita[/red] — i capi qui "
            "sotto risultano senza misure per un problema tecnico, non perché il venditore "
            "non le abbia scritte. Controlla ANTHROPIC_API_KEY e la connessione."
        )


def stampa_risultati(prodotti: list[ProdottoArricchito], query: str) -> None:
    if not prodotti:
        console.print("[yellow]Nessun risultato trovato. Prova con una ricerca diversa.[/yellow]")
        return

    table = Table(
        title=f"Risultati per: [bold]{query}[/bold]",
        show_header=True,
        header_style="bold cyan",
        show_lines=True,
        expand=True,
    )

    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Sito", width=9)
    table.add_column("Prodotto", min_width=25)
    table.add_column("Prezzo", width=8, justify="right")
    table.add_column("Taglia", width=7, justify="center")
    table.add_column("Parere", min_width=30)

    # I capi con misure dichiarate vengono prima; quelli "in forse" dopo un separatore.
    primo_in_forse = next(
        (i for i, pa in enumerate(prodotti) if pa.fit is not None and pa.fit.confidenza == 0),
        None,
    )
    ha_misurati = primo_in_forse is not None and primo_in_forse > 0

    for i, pa in enumerate(prodotti, start=1):
        p = pa.prodotto
        v = pa.valutazione

        if ha_misurati and i - 1 == primo_in_forse:
            table.add_section()
            table.add_row(
                "", "",
                Text.from_markup("[dim italic]— in forse: misure non dichiarate —[/dim italic]"),
                "", "", "",
            )

        # Colonna sito
        icona = ICONE_SITO.get(p.sito, "🔵")
        colore_sito = COLORI_SITO.get(p.sito, "white")
        sito_text = Text(f"{icona} {p.sito.capitalize()}", style=colore_sito)

        # Colonna prodotto (nome cliccabile: terminale con supporto hyperlink → apre l'URL)
        nome = p.nome[:60] + "…" if len(p.nome) > 60 else p.nome
        brand = f"\n[dim]{p.brand}[/dim]" if p.brand else ""
        condizione = f"\n[dim italic]{p.condizione}[/dim italic]" if p.condizione else ""
        prodotto_text = Text.from_markup(f"[link={p.url}]{nome}[/link]{brand}{condizione}")

        # Colonna prezzo
        if p.prezzo is not None:
            prezzo_str = f"€{p.prezzo:.0f}"
        else:
            prezzo_str = "[dim]N/D[/dim]"

        # Colonna taglia
        taglia_str = p.taglia_disponibile or "[dim]-[/dim]"

        # Colonna parere: giudizio del consulente + riga misure calcolata da fit.py
        righe_parere = []
        if v:
            colore = COLORI_RACCOMANDAZIONE.get(v.raccomandazione, "white")
            icona_racc = ICONE_RACCOMANDAZIONE.get(v.raccomandazione, "•")
            righe_parere.append(f"[{colore}]{icona_racc} {v.raccomandazione.upper()}[/{colore}]")
            righe_parere.append(f"[italic]{v.commento}[/italic]")

        if pa.fit is not None:
            testo_fit, stile_fit = etichetta_fit(pa.fit)
            if pa.fit.confidenza == 0:
                righe_parere.append(f"[{stile_fit}]{testo_fit}[/{stile_fit}]")
            else:
                righe_parere.append(
                    f"[{stile_fit}]{testo_fit}[/{stile_fit}]  [cyan]{pa.fit.dettaglio}[/cyan]"
                )

        parere = Text.from_markup("\n".join(righe_parere) if righe_parere else "[dim]—[/dim]")

        table.add_row(
            str(i),
            sito_text,
            prodotto_text,
            prezzo_str,
            taglia_str,
            parere,
        )

    console.print(table)
    console.print(
        "\n[dim]Comandi: [bold]/link N[/bold] mostra l'URL · "
        "[bold]/salva N[/bold] salva preferito · "
        "[bold]/carrello N[/bold] aggiunge al carrello · "
        "[bold]/aiuto[/bold] tutti i comandi[/dim]"
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
    siti = ", ".join(profilo.siti_attivi) if profilo.siti_attivi else "nessuno"
    gusti_pos = ", ".join(profilo.gusti_positivi) if profilo.gusti_positivi else "—"
    gusti_neg = ", ".join(profilo.gusti_negativi) if profilo.gusti_negativi else "—"

    contenuto = (
        f"[bold cyan]👤 {profilo.nome}[/bold cyan]\n\n"
        f"[underline]Misure fisiche:[/underline]\n"
        + ("\n".join(righe_fisico) if righe_fisico else "[dim]Non inserite[/dim]")
        + "\n\n[underline]Taglie abituali:[/underline]\n"
        + ("\n".join(righe_taglie) if righe_taglie else "[dim]Non inserite[/dim]")
        + f"\n\n[underline]Stile preferito:[/underline] {stili}"
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
            p.nome[:50] + "…" if len(p.nome) > 50 else p.nome,
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
