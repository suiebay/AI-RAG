import os
import re

from google import genai
from google.genai import types

MODEL = "gemini-3-flash-preview"

_client = None


def _get_client():
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY қоршаған ортада орнатылмаған")
        _client = genai.Client(api_key=api_key)
    return _client


SYSTEM_CHAT = """Сен оқушыларға көмектесетін ИИ-тьюторсың. Сенің тапсырмаң — \
мұғалім берген оқу материалдары негізінде оқушының сұрақтарына жауап беру.

Қатаң ережелер:
1. ТЕК қазақ тілінде жауап бер. Басқа тілде жазба.
2. Жауаптарың материалдарға негізделуі керек.
3. Егер сұрақ материалдарда қамтылмаса, адал айт: \
"Бұл сұрақ мұғалімнің материалдарында жоқ" деп жауап бер.
4. Жауаптарың қысқа, түсінікті, оқушыға лайық болсын.
5. ТЕК қарапайым мәтін жаз. Markdown форматын қолданба: \
жұлдызшалар (* немесе **), шеңберлер (#), астын сызулар (_), кері апострофтар (`) \
және басқа арнайы символдарды мүлдем жазба. Мәтінді қарапайым сөйлеммен құр."""


def _system_adapt(theme: str) -> str:
    return f"""Сен оқу мәтінін оқушыға жақын тақырыпқа бейімдейтін ИИ көмекшісің.

Тапсырма: берілген оқу материалын "{theme}" тақырыбы арқылы қайта жаз.

Міндетті ережелер:
1. Материалдың негізгі идеясы, логикасы және ғылыми мазмұны сақталуы керек.
2. Барлық факттер, сандар және түсініктер дұрыс қалуы тиіс.
3. Мысалдар, салыстырулар және иллюстрациялар "{theme}" саласынан алынады.
4. Жауап ТЕК қазақ тілінде болсын.
5. Оқушыға түсінікті, қызықты және жанды тілде жаз.
6. Басында қысқаша кіріспе, соңында қысқаша қорытынды болсын.
7. ТЕК қарапайым мәтін жаз. Markdown форматын қолданба: \
жұлдызшалар (* немесе **), шеңберлер (#), астын сызулар (_), кері апострофтар (`) \
және басқа арнайы символдарды мүлдем жазба. Мәтінді қарапайым абзацтармен жаз."""


def _strip_markdown(text: str) -> str:
    if not text:
        return text
    text = re.sub(r"```[\w-]*\n?", "", text)
    text = text.replace("```", "")
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\*)\*(?!\s)([^\*\n]+?)(?<!\s)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<![A-Za-zА-Яа-яӘәҒғҚқҢңӨөҰұҮүҺһІі0-9_])_(?!\s)([^_\n]+?)(?<!\s)_(?![A-Za-zА-Яа-яӘәҒғҚқҢңӨөҰұҮүҺһІі0-9_])", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _history_to_contents(history: list) -> list:
    contents = []
    for turn in (history or [])[-10:]:
        role = turn.get("role")
        text = (turn.get("content") or "").strip()
        if not text or role not in ("user", "assistant"):
            continue
        g_role = "user" if role == "user" else "model"
        contents.append(
            types.Content(role=g_role, parts=[types.Part.from_text(text=text)])
        )
    return contents


async def chat_with_context(message: str, history: list, context_text: str) -> str:
    context_block = context_text.strip() or "(мұғалім әлі материал қоспаған)"
    system = f"{SYSTEM_CHAT}\n\n=== ОҚУ МАТЕРИАЛДАРЫ ===\n{context_block}"

    contents = _history_to_contents(history)
    contents.append(
        types.Content(role="user", parts=[types.Part.from_text(text=message)])
    )

    resp = await _get_client().aio.models.generate_content(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=1024,
        ),
    )
    return _strip_markdown(resp.text or "") or "Кешіріңіз, жауап алынбады. Қайталап көріңіз."


async def adapt_content(title: str, content: str, theme: str) -> str:
    user_prompt = (
        f"Оқу материалы:\n\nТақырыбы: {title}\n\nМазмұны:\n{content}\n\n"
        f'Осы материалды "{theme}" тақырыбы арқылы қайта жаз. '
        "Негізгі идеяны жоғалтпа, бірақ барлық мысалдар мен салыстыруларды "
        f'"{theme}" саласынан ал.'
    )
    resp = await _get_client().aio.models.generate_content(
        model=MODEL,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=_system_adapt(theme),
            max_output_tokens=2048,
        ),
    )
    return _strip_markdown(resp.text or "") or "Кешіріңіз, мәтін генерациялау сәтсіз аяқталды."
