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

# Font size: GS ! n (n = (width_mag << 4) | height_mag)
# 0=1x 1=2x 2=3x 3=4x 4=5x 5=6x 6=7x 7=8x
FONT_7X     = GS  + b'\x66'   # 7x ample + 7x alt
FONT_RESET  = GS  + b'!\x00'

# Text ESC/POS per incrustar en crides MCP imprimir_text
ESC_STR     = '\x1b'
GS_STR      = '\x1d'
LF_STR      = '\n'
FONT_7X_STR = GS_STR + '!\x66'
FONT_RESET_STR = GS_STR + '!\x00'
BOLD_ON_STR = ESC_STR + 'E\x01'
BOLD_OFF_STR = ESC_STR + 'E\x00'
CENTER_STR  = ESC_STR + 'a\x01'
LEFT_STR    = ESC_STR + 'a\x00'
CUT_STR     = GS_STR  + 'V\x41\x03'


def _format_totals_escpos(data: str, totals: dict, clients_count: int) -> str:
    """Retorna text amb ESC/POS incrustat per al resum de produccio.
    El header (titol) surt amb font size 7x.
    """
    W = 28
    sep = "-" * W
    total_u = sum(totals.values())
    sorted_items = sorted(totals.items(), key=lambda x: x[0].lower())

    body = ""
    body += CENTER_STR
    body += FONT_7X_STR
    body += BOLD_ON_STR
    body += "ITERUM PANIS" + LF_STR
    body += BOLD_OFF_STR
    body += FONT_RESET_STR
    body += f"RESUM PRODUCCIO {data}" + LF_STR
    body += LF_STR

    body += LEFT_STR
    body += sep + LF_STR
    body += BOLD_ON_STR
    body += f"{'QTY':>4}  {'ARTICLE':<22}" + LF_STR
    body += BOLD_OFF_STR
    body += sep + LF_STR

    for art, qty in sorted_items:
        qty_str = str(qty)
        nom = art[:22] if len(art) > 22 else art
        body += f"{qty_str:>4}  {nom:<22}" + LF_STR

    body += sep + LF_STR
    body += BOLD_ON_STR
    body += f"TOTAL UNITATS: {total_u}" + LF_STR
    body += BOLD_OFF_STR
    body += LF_STR

    body += CENTER_STR
    body += f"Clients: {clients_count}" + LF_STR
    body += LF_STR + LF_STR + LF_STR
    body += CUT_STR

    return body


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


async def imprimir_text_directe(text_escpos: str) -> dict:
    """Envia text ESC/POS directament a la impressora per TCP.
    Així evitem la corrupcio de caracters de control per CP1252/JSON del MCP."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(PRINTER_IP, PRINTER_PORT),
            timeout=TIMEOUT,
        )
        writer.write(_encode(text_escpos))
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return {"ok": True}
    except asyncio.TimeoutError:
        return {"error": f"Timeout connectant a {PRINTER_IP}:{PRINTER_PORT}"}
    except Exception as e:
        return {"error": str(e)}


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
