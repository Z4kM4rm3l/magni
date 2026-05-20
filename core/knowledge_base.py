import os
import json
import time
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

KB_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'data', 'knowledge_base.json')
)

def _load_kb() -> list:
    try:
        if os.path.exists(KB_FILE):
            with open(KB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception:
        return []

def _save_kb(articles: list):
    os.makedirs(os.path.dirname(KB_FILE), exist_ok=True)
    with open(KB_FILE, 'w', encoding='utf-8') as f:
        json.dump(articles, f, indent=2, ensure_ascii=False)

def generate_summary(content: str) -> str:
    """Use Gemini to generate a concise cliff-note summary of an article."""
    try:
        model = genai.GenerativeModel(model_name="gemini-2.5-flash")
        prompt = f"""Summarize the following business knowledge base article in 1-2 sentences maximum.
Be concise and factual. Focus on what a customer service agent needs to know.

Article:
{content[:3000]}

Summary:"""
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return "Summary unavailable."

def add_article(title: str, content: str, category: str = "general") -> dict:
    """Add a new article to the knowledge base with AI-generated summary."""
    articles = _load_kb()

    summary = generate_summary(content)

    article = {
        "id": str(int(time.time() * 1000)),
        "title": title,
        "content": content,
        "summary": summary,
        "category": category,
        "created_at": time.time(),
        "updated_at": time.time()
    }

    articles.append(article)
    _save_kb(articles)
    return article

def get_all_articles() -> list:
    """Return all articles sorted by most recent."""
    articles = _load_kb()
    return sorted(articles, key=lambda x: x.get("updated_at", 0), reverse=True)

def get_article(article_id: str) -> dict | None:
    """Get a single article by ID."""
    articles = _load_kb()
    for article in articles:
        if article["id"] == article_id:
            return article
    return None

def update_article(article_id: str, title: str, content: str, category: str) -> dict | None:
    """Update an existing article and regenerate its summary."""
    articles = _load_kb()
    for i, article in enumerate(articles):
        if article["id"] == article_id:
            summary = generate_summary(content)
            articles[i].update({
                "title": title,
                "content": content,
                "summary": summary,
                "category": category,
                "updated_at": time.time()
            })
            _save_kb(articles)
            return articles[i]
    return None

def delete_article(article_id: str) -> bool:
    """Delete an article by ID."""
    articles = _load_kb()
    original_count = len(articles)
    articles = [a for a in articles if a["id"] != article_id]
    if len(articles) < original_count:
        _save_kb(articles)
        return True
    return False

def search_kb(query: str, max_results: int = 3) -> list:
    """
    Simple keyword search across title and content.
    Returns the most relevant articles for a given query.
    """
    articles = _load_kb()
    if not articles:
        return []

    query_words = set(query.lower().split())
    scored = []

    for article in articles:
        score = 0
        title_lower = article["title"].lower()
        content_lower = article["content"].lower()

        for word in query_words:
            if len(word) < 3:
                continue
            if word in title_lower:
                score += 3  # Title match worth more
            if word in content_lower:
                score += 1

        if score > 0:
            scored.append((score, article))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [article for _, article in scored[:max_results]]

def get_kb_context(query: str) -> str:
    """
    Build a context string from relevant KB articles for injection into Magni's prompt.
    Returns empty string if no relevant articles found.
    """
    relevant = search_kb(query)
    if not relevant:
        return ""

    context_parts = ["KNOWLEDGE BASE — Use this information to answer accurately:"]
    for article in relevant:
        context_parts.append(f"\n[{article['title']}]\n{article['content'][:800]}")

    return "\n".join(context_parts)

def get_categories() -> list:
    """Return all unique categories in the KB."""
    articles = _load_kb()
    categories = list(set(a.get("category", "general") for a in articles))
    return sorted(categories)
