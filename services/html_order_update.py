from browser import HitSystemsBrowser
from config import HITSYSTEMS_PASS, HITSYSTEMS_USER


async def update_order_line_html(pending: dict, new_quantity: int) -> dict:
    browser = HitSystemsBrowser(HITSYSTEMS_USER, HITSYSTEMS_PASS, headless=True)
    try:
        await browser.start()
        return await browser.actualitzar_linia(
            client=pending["client_name"],
            data=pending["date_display"],
            article_code=int(pending["article_code"]),
            requested=int(pending["requested"]),
            served=int(pending["served"]),
            returned=int(pending["returned"]),
            order_type=int(pending["order_type"]),
            new_quantity=new_quantity,
        )
    finally:
        await browser.close()
