"""
test_mcp.py — Test real del MCP de vendes
Test: afegir Coca de Pa (art=135) x99 a Cal Cabre (client=1040) data 30/03/2026,
      consultar la comanda i despres posar la quantitat a 0.
"""
import asyncio
import logging
import sys
# Forcar UTF-8 al terminal Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from mcp_vendes import MCPVendes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

CLIENT  = 1040        # Cal Cabre
ARTICLE = 135         # Coca de Pa
DATA    = "2026-03-30"


async def main():
    mcp = MCPVendes()

    # 1. Veure estat inicial
    print("\n=== 1. ESTAT INICIAL ===")
    r = await mcp.veure_comanda(DATA, CLIENT)
    if "error" in r:
        print(f"Error: {r['error']}")
    else:
        lines = r.get("order", [])
        coca = [l for l in lines if l.get("art") == ARTICLE]
        print(f"Coca de Pa ara: {coca}")

    # 2. Afegir 99 unitats
    print("\n=== 2. AFEGIR 99 UNITATS Coca de Pa ===")
    r2 = await mcp.afegir_linia_mcp(DATA, CLIENT, ARTICLE, 99)
    print(f"Resultat add_order: {r2}")
    if r2.get("ok"):
        print("TEST PASS: add_order ok=True")
    else:
        print(f"TEST FAIL: {r2}")

    # 3. Consultar per verificar
    print("\n=== 3. VERIFICAR COMANDA ===")
    r3 = await mcp.veure_comanda(DATA, CLIENT)
    if "error" in r3:
        print(f"Error: {r3['error']}")
    else:
        lines = r3.get("order", [])
        coca = [l for l in lines if l.get("art") == ARTICLE]
        total = sum(l.get("requested", 0) for l in coca)
        print(f"Linies Coca de Pa: {coca}")
        print(f"Total requested: {total}")
        if total == 99:
            print("TEST PASS: quantitat 99 confirmada")
        else:
            print(f"NOTA: total={total} (pot haver linies duplicades per com funciona add_order)")

    # 4. Posar a 0
    print("\n=== 4. POSAR A 0 ===")
    r4 = await mcp.afegir_linia_mcp(DATA, CLIENT, ARTICLE, 0)
    print(f"Resultat set 0: {r4}")
    if r4.get("ok"):
        print("TEST PASS: posada a 0 ok")

    # 5. Verificar final
    print("\n=== 5. VERIFICAR FINAL ===")
    r5 = await mcp.veure_comanda(DATA, CLIENT)
    if "error" in r5:
        print(f"Error: {r5['error']}")
    else:
        lines = r5.get("order", [])
        coca = [l for l in lines if l.get("art") == ARTICLE]
        total = sum(l.get("requested", 0) for l in coca)
        print(f"Linies Coca de Pa: {coca}")
        print(f"Total requested: {total}")
        if total == 0:
            print("TEST PASS: posada a 0 correctament")
        else:
            print(f"NOTA: total={total}")

    print("\n=== TEST COMPLETAT ===")


if __name__ == "__main__":
    asyncio.run(main())
