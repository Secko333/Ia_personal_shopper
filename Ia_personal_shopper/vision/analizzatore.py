"""Analisi di immagini locali tramite Claude Vision per estrarre descrizioni di capi."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import anthropic

from Ia_personal_shopper.config import MODELLO_VISION

FORMATI_SUPPORTATI = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

PROMPT_ANALISI = (
    "Sei un assistente di moda italiano. Analizza attentamente questo capo d'abbigliamento "
    "e fornisci una descrizione utile per cercarlo su siti di shopping online. "
    "Includi: tipo di capo (es. giacca, felpa, pantaloni), colori principali, "
    "materiale apparente, stile (casual/elegante/sportivo/streetwear), "
    "e qualsiasi dettaglio rilevante (lunghezza, fit, dettagli particolari). "
    "Rispondi in italiano con 2-3 righe concise, come se stessi descrivendo l'articolo "
    "in una barra di ricerca di un sito di moda."
)


def _leggi_immagine(path: Path) -> tuple[str, str]:
    """Legge file immagine e ritorna (base64_data, media_type)."""
    suffisso = path.suffix.lower()

    if suffisso in FORMATI_SUPPORTATI:
        media_type = FORMATI_SUPPORTATI[suffisso]
        dati = path.read_bytes()
    else:
        # Prova a convertire con Pillow se formato non standard
        try:
            from PIL import Image
            img = Image.open(path)
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG")
            dati = buf.getvalue()
            media_type = "image/jpeg"
        except Exception as e:
            raise ValueError(
                f"Formato immagine non supportato: {suffisso}. "
                f"Formati validi: {', '.join(FORMATI_SUPPORTATI)}"
            ) from e

    return base64.standard_b64encode(dati).decode("utf-8"), media_type


async def descrivi_immagine(path_immagine: str) -> str:
    """Analizza un'immagine locale e restituisce una descrizione testuale del capo.

    Args:
        path_immagine: Percorso assoluto o relativo all'immagine.

    Returns:
        Descrizione in italiano del capo d'abbigliamento.

    Raises:
        FileNotFoundError: Se il file non esiste.
        ValueError: Se il formato non è supportato.
    """
    path = Path(path_immagine).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Immagine non trovata: {path}")

    b64, media_type = _leggi_immagine(path)

    client = anthropic.AsyncAnthropic()
    risposta = await client.messages.create(
        model=MODELLO_VISION,
        max_tokens=512,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": PROMPT_ANALISI,
                    },
                ],
            }
        ],
    )

    return risposta.content[0].text.strip()


PROMPT_OUTFIT = (
    "Sei un assistente di moda italiano. Questa è una foto di ispirazione: un outfit o un capo "
    "che piace all'utente. Individua OGNI capo d'abbigliamento distinto indossato/mostrato "
    "(es. giacca, maglia, pantaloni, scarpe) e per ciascuno scrivi UNA query di ricerca concisa "
    "per trovarne uno SIMILE su un sito di moda: includi tipo, colore, materiale apparente e stile. "
    "Ignora accessori minori (gioielli, orologi). Se c'è un solo capo, restituisci una sola query. "
    "Rispondi con UNA query per riga, nient'altro (niente numeri, niente trattini)."
)


async def estrai_capi_da_outfit(path_immagine: str) -> list[str]:
    """Scompone una foto di outfit/ispirazione in una lista di query di ricerca (una per capo)."""
    path = Path(path_immagine).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Immagine non trovata: {path}")

    b64, media_type = _leggi_immagine(path)

    client = anthropic.AsyncAnthropic()
    risposta = await client.messages.create(
        model=MODELLO_VISION,
        max_tokens=512,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                    {"type": "text", "text": PROMPT_OUTFIT},
                ],
            }
        ],
    )
    testo = risposta.content[0].text.strip()
    capi = []
    for riga in testo.splitlines():
        # ripulisce eventuali bullet/numeri iniziali che il modello potrebbe aggiungere
        riga = riga.lstrip("-•*0123456789. ").strip()
        if riga:
            capi.append(riga)
    return capi


PROMPT_STILE = (
    "Sei uno stylist. Osserva questo capo o outfit di ispirazione e deduci lo STILE personale "
    "di chi lo ama. Elenca da 3 a 6 descrittori di stile brevi (1-2 parole ciascuno), in italiano, "
    "utili come tag di preferenza (es. 'minimal', 'streetwear', 'toni neutri', 'oversize', 'vintage'). "
    "Rispondi SOLO con i descrittori separati da virgola, niente altro testo."
)


async def estrai_stile_da_immagine(path_immagine: str) -> list[str]:
    """Analizza una foto di ispirazione e restituisce descrittori di stile per il profilo gusti."""
    path = Path(path_immagine).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Immagine non trovata: {path}")

    b64, media_type = _leggi_immagine(path)

    client = anthropic.AsyncAnthropic()
    risposta = await client.messages.create(
        model=MODELLO_VISION,
        max_tokens=256,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                    {"type": "text", "text": PROMPT_STILE},
                ],
            }
        ],
    )
    testo = risposta.content[0].text.strip()
    return [tag.strip().lower() for tag in testo.split(",") if tag.strip()]
