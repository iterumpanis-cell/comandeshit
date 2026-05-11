"""
print_resum.py — Imprimeix resum per productes d'una data amb font size x7
Us: python print_resum.py [DATA]    (DATA per defecte: 12/05/2026)
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from mcp_vendes import MCPVendes
from printer import _format_totals_escpos


async def main():
    data = sys.argv[1] if len(sys.argv) > 1 else "12/05/2026"
    data_mcp = data.split("/")
    data_mcp = f"{data_mcp[2]}-{data_mcp[1]}-{data_mcp[0]}" if len(data_mcp) == 3 else data

    mcp = MCPVendes()
    result = await mcp.comandes_per_data(data_mcp)
    totals = result.get("totals_per_article", {})
    clients = result.get("clients", [])

    if not totals:
        print(f"No hi ha dades per al {data}")
        return

    text_escpos = _format_totals_escpos(data, totals, len(clients))
    res = await mcp.imprimir_text(text_escpos)

    if "error" in res:
        print(f"Error d'impressio: {res['error']}")
    else:
        total_u = sum(totals.values())
        print(f"Resum enviat a imprimir: {total_u} unitats, {len(totals)} productes")


if __name__ == "__main__":
    asyncio.run(main())
