"""Operacions d'escriptura d'ordres amb validació comuna."""


class OrderService:
    def __init__(self, mcp, logger):
        self._mcp = mcp
        self._logger = logger

    async def update_line(self, date, client, article_code, order_type, **fields):
        if not client or not article_code:
            return {"error": "client i article són obligatoris"}
        if order_type not in (1, 2, 3):
            return {"error": f"tipus d'ordre no vàlid: {order_type}"}
        self._logger.info(
            "order_service: update client=%s article=%s type=%s fields=%s",
            client, article_code, order_type, sorted(fields),
        )
        return await self._mcp.canviar_linia_mcp(
            date, client, article_code, order_type, **fields
        )

    async def add_line(self, date, client, article_code, quantity, order_type):
        return await self._mcp.afegir_linia_mcp(
            date, client, article_code, quantity, order_type
        )

    async def cancel_line(self, date, client, article_code, order_type):
        return await self._mcp.anul_linia_mcp(date, client, article_code, order_type)
