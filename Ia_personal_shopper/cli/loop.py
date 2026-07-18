"""Loop REPL principale della CLI."""

from __future__ import annotations

import asyncio
import re

from rich.prompt import Confirm, Prompt
from rich.status import Status

from Ia_personal_shopper.browser import vinted as vinted_browser
from Ia_personal_shopper.browser.agente_base import crea_agente_carrello
from Ia_personal_shopper.cli.display import (
    console,
    stampa_aiuto,
    stampa_banner,
    stampa_preferiti,
    stampa_profilo,
    stampa_risultati,
    stampa_siti,
)
from Ia_personal_shopper.config import SITI_SUPPORTATI
from Ia_personal_shopper.models import ProdottoArricchito
from Ia_personal_shopper.profilo.gestore import (
    aggiungi_gusti,
    aggiungi_preferito,
    aggiungi_stile,
    carica_preferiti,
    carica_profilo,
    salva_profilo,
)
from Ia_personal_shopper.ricerca.aggregatore import estrai_budget, pulisci_query, rileva_genere
from Ia_personal_shopper.ricerca.coordinatore import cerca_su_tutti_i_siti
from Ia_personal_shopper.valutazione.consulente import valuta_prodotti
from Ia_personal_shopper.vision.analizzatore import descrivi_immagine, estrai_stile_da_immagine

# ---------------------------------------------------------------------------
# Stato sessione
# ---------------------------------------------------------------------------

_ultima_ricerca: list[ProdottoArricchito] = []
_ultima_query: str = ""


# ---------------------------------------------------------------------------
# Handler comandi
# ---------------------------------------------------------------------------

async def _cmd_ricerca(testo: str) -> None:
    global _ultima_ricerca, _ultima_query

    profilo = carica_profilo()
    budget = estrai_budget(testo)
    query = pulisci_query(testo)
    genere = rileva_genere(testo, default=profilo.genere)

    # Usa budget default del profilo se non specificato nella query
    if budget is None and profilo.budget_default:
        budget = profilo.budget_default
        console.print(f"[dim]Budget non specificato, uso il default del profilo: €{budget:.0f}[/dim]")

    siti_str = ", ".join(profilo.siti_attivi)
    console.print(f"\n[cyan]🔍 Cerco su {siti_str}...[/cyan]")
    if budget:
        console.print(f"[dim]Budget massimo: €{budget:.0f}[/dim]")
    if genere:
        console.print(f"[dim]Genere: {genere}[/dim]")

    with Status("[cyan]Navigazione in corso...[/cyan]", console=console):
        prodotti_raw = await cerca_su_tutti_i_siti(query, budget, profilo, genere)

    if not prodotti_raw:
        console.print("[yellow]Nessun risultato. Prova con parole diverse o amplia il budget.[/yellow]")
        return

    console.print(f"[dim]💭 Elaboro le valutazioni per {len(prodotti_raw)} prodotti...[/dim]")
    prodotti_arricchiti = await valuta_prodotti(prodotti_raw, profilo, budget)

    _ultima_ricerca = prodotti_arricchiti
    _ultima_query = testo

    stampa_risultati(prodotti_arricchiti, testo)


async def _cmd_foto(argomenti: str) -> None:
    """Flow: /foto /percorso/immagine.jpg [budget N€]"""
    global _ultima_ricerca, _ultima_query

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
            descrizione = await descrivi_immagine(path_immagine)
    except FileNotFoundError as e:
        console.print(f"[red]File non trovato: {e}[/red]")
        return
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return

    console.print(f"[green]📝 Descrizione rilevata:[/green] [italic]{descrizione}[/italic]\n")

    # Aggiungi il budget alla query se presente
    query = descrizione
    if match_budget:
        query = f"{descrizione}, {budget_str}"

    await _cmd_ricerca(query)


def _cmd_salva(argomenti: str) -> None:
    global _ultima_ricerca, _ultima_query

    if not _ultima_ricerca:
        console.print("[yellow]Nessuna ricerca attiva. Esegui prima una ricerca.[/yellow]")
        return

    try:
        n = int(argomenti.strip())
    except ValueError:
        console.print("[red]Specifica il numero del prodotto: /salva 3[/red]")
        return

    if n < 1 or n > len(_ultima_ricerca):
        console.print(f"[red]Numero non valido. Scegli tra 1 e {len(_ultima_ricerca)}.[/red]")
        return

    prodotto = _ultima_ricerca[n - 1].prodotto
    aggiungi_preferito(prodotto, _ultima_query)
    console.print(
        f"[green]✅ Salvato nei preferiti:[/green] {prodotto.nome} "
        f"(€{prodotto.prezzo:.0f})" if prodotto.prezzo else f"[green]✅ Salvato nei preferiti:[/green] {prodotto.nome}"
    )


def _prodotto_da_indice(argomenti: str):
    """Ritorna il ProdottoRisultato all'indice N dall'ultima ricerca, o None con messaggio d'errore."""
    if not _ultima_ricerca:
        console.print("[yellow]Nessuna ricerca attiva. Esegui prima una ricerca.[/yellow]")
        return None
    try:
        n = int(argomenti.strip())
    except ValueError:
        console.print("[red]Specifica il numero del prodotto (es. 3)[/red]")
        return None
    if n < 1 or n > len(_ultima_ricerca):
        console.print(f"[red]Numero non valido. Scegli tra 1 e {len(_ultima_ricerca)}.[/red]")
        return None
    return _ultima_ricerca[n - 1].prodotto


def _cmd_feedback(argomenti: str, positivo: bool) -> None:
    """Registra un gusto appreso dal prodotto N (segnale: il brand)."""
    prodotto = _prodotto_da_indice(argomenti)
    if prodotto is None:
        return
    segnale = prodotto.brand or prodotto.nome
    if positivo:
        aggiungi_gusti(positivi=[segnale])
        console.print(f"[green]👍 Registrato: ti piace [bold]{segnale}[/bold]. Ne terrò conto.[/green]")
    else:
        aggiungi_gusti(negativi=[segnale])
        console.print(f"[yellow]👎 Registrato: eviterò [bold]{segnale}[/bold] in futuro.[/yellow]")


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

    prompt = (
        "Da queste risposte di un utente su gusti e stile, estrai due liste JSON di tag brevi "
        "(1-2 parole, in italiano): 'stile' (descrittori positivi di stile/preferenze) e "
        "'da_evitare' (cose che l'utente non vuole). Rispondi SOLO con JSON "
        '{"stile": [...], "da_evitare": [...]}.\n\nRisposte:\n' + "\n".join(risposte)
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
        stile = dati.get("stile", [])
        da_evitare = dati.get("da_evitare", [])
    except Exception as e:
        console.print(f"[red]Errore nell'elaborazione: {e}[/red]")
        return

    if stile:
        aggiungi_stile(stile)
    if da_evitare:
        aggiungi_gusti(negativi=da_evitare)
    console.print(f"[green]✅ Profilo aggiornato.[/green] Stile: {', '.join(stile) or '—'}")
    if da_evitare:
        console.print(f"[dim]Da evitare: {', '.join(da_evitare)}[/dim]")


async def _cmd_carrello(argomenti: str) -> None:
    global _ultima_ricerca

    if not _ultima_ricerca:
        console.print("[yellow]Nessuna ricerca attiva. Esegui prima una ricerca.[/yellow]")
        return

    try:
        n = int(argomenti.strip())
    except ValueError:
        console.print("[red]Specifica il numero del prodotto: /carrello 3[/red]")
        return

    if n < 1 or n > len(_ultima_ricerca):
        console.print(f"[red]Numero non valido. Scegli tra 1 e {len(_ultima_ricerca)}.[/red]")
        return

    pa = _ultima_ricerca[n - 1]
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
    t.pantaloni = Prompt.ask("  Taglia pantaloni (es. 32, 34)", default=t.pantaloni or "") or None
    t.scarpe = Prompt.ask("  Numero scarpe", default=t.scarpe or "") or None

    # Stile e budget
    console.print("\n[underline]Preferenze[/underline]")
    genere_str = Prompt.ask(
        "  Genere per il filtro ricerche (uomo/donna, Invio per nessun filtro)",
        default=profilo.genere or "",
    ).strip().lower()
    profilo.genere = genere_str if genere_str in ("uomo", "donna") else None

    stili_str = Prompt.ask(
        "  Stili preferiti (separati da virgola)",
        default=", ".join(profilo.preferenze_stile),
    )
    profilo.preferenze_stile = [s.strip() for s in stili_str.split(",") if s.strip()]

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

        # startswith + strip: così "/salva" senza numero mostra il messaggio d'uso
        # dell'handler invece di "comando non riconosciuto".
        elif testo == "/salva" or testo.startswith("/salva "):
            _cmd_salva(testo[len("/salva"):].strip())

        elif testo == "/carrello" or testo.startswith("/carrello "):
            await _cmd_carrello(testo[len("/carrello"):].strip())

        elif testo == "/foto" or testo.startswith("/foto "):
            await _cmd_foto(testo[len("/foto"):].strip())

        elif testo.startswith("/stile"):
            await _cmd_stile(testo[len("/stile"):])

        elif testo == "/mipiace" or testo.startswith("/mipiace "):
            _cmd_feedback(testo[len("/mipiace"):].strip(), positivo=True)

        elif testo == "/nonmipiace" or testo.startswith("/nonmipiace "):
            _cmd_feedback(testo[len("/nonmipiace"):].strip(), positivo=False)

        elif testo.startswith("/"):
            console.print(f"[red]Comando non riconosciuto: {testo}[/red] — usa [bold]/aiuto[/bold]")

        else:
            await _cmd_ricerca(testo)
