"""Test directe: cerca 'cal cabre' i veure comanda 2026-05-01"""
import asyncio
import sys
sys.path.insert(0, r"C:\botTel")
from mcp_vendes import MCPVendes

async def main():
    mcp = MCPVendes()

    print("=== search_client 'cal cabre' ===")
    clients = await mcp.cercar_client("cal cabre")
    print(f"Resultats: {clients}")

    if clients:
        for c in clients:
            codi = c.get("c") or c.get("code")
            nom = c.get("n") or c.get("name")
            print(f"\n=== view_order client={codi} ({nom}) data=2026-05-01 ===")
            r = await mcp.veure_comanda("2026-05-01", codi)
            print(f"Resultat: {r}")

asyncio.run(main())
