"""Lettura dei cookie di sessione Vinted dal profilo Chrome dell'utente (solo macOS).

Perché serve: l'utente è già loggato su Vinted nel suo Chrome di tutti i giorni. Farlo
riaccedere in un profilo separato è inutile, e soprattutto richiede di lanciare un browser
automatizzato — che DataDome riconosce e blocca. Leggendo i cookie da qui non si apre
nessun browser: non c'è niente da riconoscere.

Cosa fa e cosa non fa, esplicitamente:
- copia il database dei cookie (Chrome lo tiene aperto) e interroga SOLO le righe di
  vinted.it, e di quelle SOLO i due token di sessione che servono all'API;
- non legge, non copia e non registra nessun altro cookie né nessun altro dato di Chrome;
- non tocca password: i token di sessione non sono la password dell'utente e non
  permettono di ricavarla;
- non stampa né logga i valori.

La chiave di cifratura sta nel Keychain di macOS: la prima lettura fa comparire il dialogo
di sistema che chiede il permesso, e va approvato dall'utente.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

# Solo questi vengono estratti: sono i cookie JWT che autenticano le richieste API.
COOKIE_AUTH = ("access_token_web", "refresh_token_web")

_CHROME_DIR = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"

# Parametri della cifratura dei cookie Chrome su macOS (schema "v10").
_SALT = b"saltysalt"
_ITERAZIONI = 1003
_LUNGHEZZA_CHIAVE = 16
_IV = b" " * 16
_PREFISSO_V10 = b"v10"
# Le versioni recenti di Chrome premettono al testo in chiaro l'hash SHA-256 del dominio.
_LUNGHEZZA_HASH_DOMINIO = 32


def _password_keychain() -> str | None:
    """La password "Chrome Safe Storage" dal Keychain. Fa comparire il dialogo di sistema."""
    for argomenti in (
        ["-s", "Chrome Safe Storage", "-a", "Chrome"],
        ["-s", "Chrome Safe Storage"],
    ):
        try:
            esito = subprocess.run(
                ["security", "find-generic-password", "-w", *argomenti],
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if esito.returncode == 0 and esito.stdout.strip():
            return esito.stdout.strip()
    return None


def _chiave(password: str) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA1(), length=_LUNGHEZZA_CHIAVE,
        salt=_SALT, iterations=_ITERAZIONI,
    )
    return kdf.derive(password.encode())


def _decifra(valore: bytes, chiave: bytes) -> str | None:
    """Decifra un encrypted_value in schema v10. None se non è decifrabile."""
    if not valore.startswith(_PREFISSO_V10):
        return None                      # v20 = App-Bound Encryption, non apribile da fuori

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    try:
        decifratore = Cipher(algorithms.AES(chiave), modes.CBC(_IV)).decryptor()
        chiaro = decifratore.update(valore[len(_PREFISSO_V10):]) + decifratore.finalize()
    except Exception:
        return None

    if not chiaro:
        return None
    # PKCS7: l'ultimo byte dice quanti byte di padding togliere.
    padding = chiaro[-1]
    if 1 <= padding <= 16:
        chiaro = chiaro[:-padding]

    testo = chiaro.decode("utf-8", "ignore")
    # Chrome recente premette l'hash del dominio: se il risultato non è stampabile, si
    # riprova saltandolo. I token Vinted sono JWT, quindi ASCII.
    if not testo.isprintable() and len(chiaro) > _LUNGHEZZA_HASH_DOMINIO:
        testo = chiaro[_LUNGHEZZA_HASH_DOMINIO:].decode("utf-8", "ignore")
    return testo if testo.isprintable() and testo else None


def _profili() -> list[Path]:
    """I profili Chrome che contengono un database di cookie."""
    if not _CHROME_DIR.is_dir():
        return []
    return [d / "Cookies" for d in _CHROME_DIR.iterdir() if (d / "Cookies").is_file()]


def cookie_vinted() -> dict[str, str]:
    """{nome: valore} dei soli token di sessione Vinted trovati nel Chrome dell'utente.

    Dizionario vuoto se Chrome non c'è, se l'utente non è loggato su Vinted, se il Keychain
    non viene autorizzato, o se i cookie usano uno schema non decifrabile.
    """
    if sys.platform != "darwin":
        return {}                        # su altri sistemi il Keychain non esiste
    profili = _profili()
    if not profili:
        return {}

    password = _password_keychain()
    if not password:
        return {}
    try:
        chiave = _chiave(password)
    except Exception:
        return {}

    segnaposto = ",".join("?" * len(COOKIE_AUTH))
    for db in profili:
        cartella = tempfile.mkdtemp()
        try:
            # Copia: Chrome tiene il file aperto e SQLite lo troverebbe bloccato.
            copia = os.path.join(cartella, "cookies.db")
            shutil.copy2(db, copia)
            con = sqlite3.connect(copia)
            try:
                righe = con.execute(
                    "SELECT name, encrypted_value FROM cookies "
                    f"WHERE host_key LIKE '%vinted%' AND name IN ({segnaposto})",
                    COOKIE_AUTH,
                ).fetchall()
            finally:
                con.close()
        except Exception:
            continue
        finally:
            shutil.rmtree(cartella, ignore_errors=True)

        trovati = {}
        for nome, cifrato in righe:
            valore = _decifra(bytes(cifrato), chiave)
            if valore:
                trovati[nome] = valore
        if trovati:
            return trovati                # primo profilo utile: gli altri non si aprono
    return {}


if __name__ == "__main__":
    # Self-check: verifica il percorso completo senza mai stampare i valori.
    # Richiede Chrome, una sessione Vinted attiva e l'approvazione del dialogo Keychain.
    print(f"sistema supportato : {sys.platform == 'darwin'}")
    print(f"profili Chrome     : {len(_profili())}")

    pwd = _password_keychain()
    print(f"chiave dal Keychain: {'ottenuta' if pwd else 'NEGATA o assente'}")
    if not pwd:
        print("\nSe è comparso un dialogo, va approvato. Se non è comparso, Chrome non ha")
        print("una voce 'Chrome Safe Storage' nel Keychain.")
        raise SystemExit(1)

    cookie = cookie_vinted()
    print(f"token trovati      : {sorted(cookie)}")
    for nome, valore in cookie.items():
        # Solo forma e lunghezza: i valori sono credenziali di sessione, non si stampano.
        assert valore.isprintable(), f"{nome}: decifratura sbagliata (testo non stampabile)"
        assert len(valore) > 20, f"{nome}: valore troppo corto per essere un token"
        print(f"  {nome:20} {len(valore):4} caratteri · sembra JWT: {valore.startswith('ey')}")

    assert cookie, "nessun token: non loggato su Vinted in Chrome, o Keychain negato"
    print("\nOK")
