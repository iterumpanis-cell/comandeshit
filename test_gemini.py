"""
Test de connexió Gemini. Executa'l abans d'arrencar el bot.
Ús: python test_gemini.py
"""
import sys
from dotenv import load_dotenv
import os

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY", "")
PRIMARY = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
FALLBACKS = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "").split(",") if m.strip()]
ALL_MODELS = [PRIMARY] + [m for m in FALLBACKS if m != PRIMARY]

PROMPT = "Respon amb una sola paraula en català: funciona"


def test_model(client, model_name: str) -> tuple[bool, str]:
    try:
        resp = client.models.generate_content(model=model_name, contents=[PROMPT])
        text = (resp.text or "").strip()
        return True, text
    except Exception as exc:
        code = getattr(getattr(exc, "status_code", None), "value", None) or str(exc)[:60]
        return False, str(code)


def main():
    if not API_KEY:
        print("ERROR: GEMINI_API_KEY no trobada al .env")
        sys.exit(1)

    try:
        from google import genai
    except ImportError:
        print("ERROR: paquet google-genai no instal·lat. Executa: pip install google-genai")
        sys.exit(1)

    client = genai.Client(api_key=API_KEY)
    print(f"API key: {API_KEY[:8]}...{API_KEY[-4:]}")
    print(f"Models a provar: {ALL_MODELS}\n")

    ok_count = 0
    first_ok = None
    for model in ALL_MODELS:
        tag = "(primari)" if model == PRIMARY else "(fallback)"
        ok, result = test_model(client, model)
        status = "OK " if ok else "ERR"
        print(f"  [{status}] {model} {tag}: {result}")
        if ok:
            ok_count += 1
            if first_ok is None:
                first_ok = model

    print()
    if first_ok:
        print(f"Resultat: {ok_count}/{len(ALL_MODELS)} models disponibles. Primer OK: {first_ok}")
        if first_ok != PRIMARY:
            print(f"ATENCIO: el model primari ({PRIMARY}) no respon. El bot usara {first_ok} com a fallback.")
        sys.exit(0)
    else:
        print("ERROR CRITIC: cap model Gemini disponible. El bot no podrà respondre.")
        sys.exit(1)


if __name__ == "__main__":
    main()
