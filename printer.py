"""
printer.py — Impressió directa a impressora Star IFBD-HI01X/02 via TCP port 9100
Usa comandes ESC/POS compatibles amb Star.
"""
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

PRINTER_IP   = "192.168.1.43"
PRINTER_PORT = 9100
TIMEOUT      = 10  # segons

# ESC/POS
ESC = b'\x1b'
GS  = b'\x1d'

INIT        = ESC + b'@'
BOLD_ON     = ESC + b'E\x01'
BOLD_OFF    = ESC + b'E\x00'
ALIGN_LEFT  = ESC + b'a\x00'
ALIGN_CENTER= ESC + b'a\x01'
ALIGN_RIGHT = ESC + b'a\x02'
DOUBLE_ON   = GS  + b'!\x11'   # doble ample + doble alt
DOUBLE_OFF  = GS  + b'!\x00'
CUT         = GS  + b'V\x41\x03'  # tall parcial amb feed
LF          = b'\n'


def _encode(text: str) -> bytes:
    return text.encode("cp858", errors="replace")


def _format_ticket(client_name: str, data: str, linies: list) -> bytes:
    """Genera els bytes ESC/POS de l'albarà."""
    buf = bytearray()
    buf += INIT
    buf += ALIGN_CENTER
    buf += BOLD_ON + DOUBLE_ON
    buf += _encode("ITERUM PANIS") + LF
    buf += DOUBLE_OFF + BOLD_OFF
    buf += _encode("Obrador") + LF
    buf += LF

    buf += BOLD_ON
    buf += ALIGN_LEFT
    buf += _encode(f"Client: {client_name}") + LF
    buf += _encode(f"Data:   {data}") + LF
    buf += BOLD_OFF
    buf += _encode("-" * 40) + LF

    # Capçalera columnes
    buf += BOLD_ON
    buf += _encode(f"{'QTY':>4}  ARTICLE") + LF
    buf += BOLD_OFF
    buf += _encode("-" * 40) + LF

    total = 0
    for linia in linies:
        qty  = linia.get("requested", 0)
        if not qty:
            continue
        nom  = linia.get("nm") or linia.get("artName") or linia.get("name") or "?"
        enc  = "*" if linia.get("order_type") == 2 else " "
        # Trunca nom si és massa llarg
        nom_t = nom[:33] if len(nom) > 33 else nom
        buf += _encode(f"{enc}{qty:>3}  {nom_t}") + LF
        total += qty

    buf += _encode("-" * 40) + LF
    buf += BOLD_ON
    buf += _encode(f"TOTAL: {total} unitats") + LF
    buf += BOLD_OFF
    buf += LF
    buf += ALIGN_CENTER
    buf += _encode(datetime.now().strftime("%d/%m/%Y %H:%M")) + LF
    buf += LF + LF + LF
    buf += CUT

    return bytes(buf)


async def imprimir_albara(client_name: str, data: str, linies: list, copies: int = 1) -> dict:
    """
    Envia l'albarà directament a la impressora Star via TCP.
    Retorna {"ok": True} o {"error": "..."}
    """
    ticket = _format_ticket(client_name, data, linies)
    try:
        for i in range(copies):
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(PRINTER_IP, PRINTER_PORT),
                timeout=TIMEOUT,
            )
            writer.write(ticket)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            logger.info(f"Impressió {i+1}/{copies} enviada: {client_name} ({data})")
        return {"ok": True}
    except asyncio.TimeoutError:
        msg = f"Timeout connectant a la impressora {PRINTER_IP}:{PRINTER_PORT}"
        logger.error(msg)
        return {"error": msg}
    except Exception as e:
        logger.error(f"Error impressió: {e}")
        return {"error": str(e)}
