import httpx
import json
import html
import urllib.parse
from typing import Optional, Dict
from bs4 import BeautifulSoup
from google import genai
from google.genai import types as gtypes
from prompts import SYSTEM_PROMPT, build_user_prompt
import os

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    return _client


def _fetch_url(url: str) -> str:
    headers = {
        # iPhone UA gives Instagram og: tags without login wall
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as c:
            resp = c.get(url, headers=headers)

        soup = BeautifulSoup(resp.text, "html.parser")
        result = {}

        for meta in soup.find_all("meta"):
            prop = meta.get("property") or meta.get("name")
            content = meta.get("content")
            if prop and content and prop.startswith("og:") and len(content) < 3000:
                result[prop] = html.unescape(content)

        if soup.title:
            result["page_title"] = html.unescape(soup.title.string or "")

        for s in soup.find_all("script", type="application/ld+json")[:2]:
            try:
                data = json.loads(s.string or "")
                result["structured_data"] = data
                break
            except Exception:
                pass

        if not result:
            return f"Данные не получены (статус {resp.status_code})."

        return json.dumps(result, ensure_ascii=False, indent=2)[:8000]
    except Exception as e:
        return f"Ошибка загрузки {url}: {e}"


def _search_ddg(query: str) -> list:
    from duckduckgo_search import DDGS
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=8))


def _search_google(query: str) -> list:
    url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&num=10&hl=ru"
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    with httpx.Client(timeout=15, follow_redirects=True) as c:
        resp = c.get(url, headers=headers)
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for el in soup.select("a[href*='/url?q=']")[:20]:
        href = el.get("href", "")
        if "/url?q=" in href:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            href = qs.get("q", [""])[0]
        if href.startswith("http") and "google.com" not in href:
            title = el.get_text(strip=True)[:120]
            if title:
                results.append({"title": title, "href": href, "body": ""})
    return results[:8]


def _search_web(query: str) -> str:
    results = []
    try:
        results = _search_ddg(query)
    except Exception:
        pass
    if not results:
        try:
            results = _search_google(query)
        except Exception as e:
            return f"Поиск недоступен: {e}. Используй свои знания о нише."
    if not results:
        return "Поиск не дал результатов. Используй свои знания о нише."
    return json.dumps(results, ensure_ascii=False, indent=2)[:6000]


# Tool definitions for Gemini
TOOLS = [
    gtypes.Tool(function_declarations=[
        gtypes.FunctionDeclaration(
            name="fetch_url",
            description="Загружает HTML страницы и возвращает og:-метатеги. Используй для получения данных Instagram-профилей.",
            parameters=gtypes.Schema(
                type=gtypes.Type.OBJECT,
                properties={
                    "url": gtypes.Schema(
                        type=gtypes.Type.STRING,
                        description="URL для загрузки"
                    )
                },
                required=["url"]
            )
        ),
        gtypes.FunctionDeclaration(
            name="search_web",
            description="Ищет в интернете. Используй для поиска конкурентов и Instagram-профилей в нише.",
            parameters=gtypes.Schema(
                type=gtypes.Type.OBJECT,
                properties={
                    "query": gtypes.Schema(
                        type=gtypes.Type.STRING,
                        description="Поисковый запрос"
                    )
                },
                required=["query"]
            )
        )
    ])
]


async def analyze_instagram(nick: str, progress_callback=None, qual_answers: Optional[Dict[str, str]] = None) -> str:
    client = _get_client()

    user_prompt = build_user_prompt(nick, qual_answers)

    contents = [
        gtypes.Content(
            role="user",
            parts=[gtypes.Part(text=user_prompt)]
        )
    ]

    tool_call_count = 0

    while True:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=gtypes.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                tools=TOOLS,
                temperature=0.7,
                max_output_tokens=8192,
            )
        )

        candidate = response.candidates[0]
        contents.append(gtypes.Content(role="model", parts=candidate.content.parts))

        # Check if there are tool calls
        tool_calls = [p for p in candidate.content.parts if p.function_call is not None]

        if not tool_calls:
            # No tool calls — extract text response
            text_parts = [p.text for p in candidate.content.parts if hasattr(p, "text") and p.text]
            if text_parts:
                return "\n".join(text_parts)
            return "Анализ завершён, но ответ пустой."

        # Process tool calls
        tool_call_count += len(tool_calls)
        if tool_call_count > 20:
            # Force finish
            contents.append(gtypes.Content(
                role="user",
                parts=[gtypes.Part(text="Достаточно данных. Напиши финальный отчёт.")]
            ))
            continue

        tool_results = []
        for part in candidate.content.parts:
            if part.function_call is None:
                continue
            fc = part.function_call
            name = fc.name
            args = dict(fc.args) if fc.args else {}

            if name == "fetch_url":
                url = args.get("url", "")
                if progress_callback:
                    await progress_callback(f"🔍 Загружаю {url[:50]}...")
                result = _fetch_url(url)
            elif name == "search_web":
                q = args.get("query", "")
                if progress_callback:
                    await progress_callback(f"🔎 Ищу: {q[:60]}...")
                result = _search_web(q)
            else:
                result = f"Неизвестный инструмент: {name}"

            tool_results.append(
                gtypes.Part(
                    function_response=gtypes.FunctionResponse(
                        name=name,
                        response={"result": result}
                    )
                )
            )

        contents.append(gtypes.Content(role="user", parts=tool_results))
