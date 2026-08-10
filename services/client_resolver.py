"""Resolució compartida de clients per als fluxos conversacionals."""


class ClientResolver:
    def __init__(self, mcp, logger, limit: int = 8):
        self._mcp = mcp
        self._logger = logger
        self._limit = limit

    async def resolve(self, text: str) -> list[tuple[str, int]]:
        query = (text or "").strip()
        if not query:
            return []
        try:
            resultats = await self._mcp.cercar_client(query)
        except Exception as exc:
            self._logger.warning("client_resolver: error cercant %r: %s", query, exc)
            return []

        options = []
        seen = set()
        for item in resultats or []:
            name = item.get("n") or item.get("name")
            code = item.get("c") or item.get("code")
            if not name or code is None or code in seen:
                continue
            seen.add(code)
            options.append((str(name), int(code)))
        return options[:self._limit]
