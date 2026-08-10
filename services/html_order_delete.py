from browser import HitSystemsBrowser
from config import HITSYSTEMS_PASS, HITSYSTEMS_USER


async def delete_order_line_html(pending: dict) -> dict:
    browser = HitSystemsBrowser(HITSYSTEMS_USER, HITSYSTEMS_PASS, headless=True)
    try:
        await browser.start()
        return await browser.esborrar_linia(
            client=pending["client_name"],
            data=pending["date_display"],
            article_code=int(pending["article_code"]),
            requested=int(pending["requested"]),
            served=int(pending["served"]),
            returned=int(pending["returned"]),
            order_type=int(pending["order_type"]),
        )
    finally:
        await browser.close()
