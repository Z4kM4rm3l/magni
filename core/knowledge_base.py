"""
core/knowledge_base.py

Tenant-scoped, PostgreSQL-backed knowledge base service.

Every public function takes client_id as its first argument and every database
access filters on it. There is deliberately no function that reads, updates, or
deletes an article by id alone: an article is always addressed by the
(client_id, article_id) pair, so one tenant can never reach another tenant's
knowledge. This is the core isolation invariant of the KB.

Signatures mirror the previous JSON-backed module with client_id prepended.
Return shapes match the old dict shape (epoch-float timestamps, via .timestamp()
as core/client_manager.py does) so the admin UI keeps working unchanged.
"""
import os
import uuid
from datetime import datetime, timezone

import google.generativeai as genai
from dotenv import load_dotenv

from core.db import SessionLocal
from core.models import KnowledgeArticle
from core.utils import logger

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))


# ── Serialization ───────────────────────────────────────────────────────────
def _to_dict(a: KnowledgeArticle) -> dict:
    """ORM row -> the dict shape the admin UI and prompt builder expect."""
    return {
        "id":         a.id,
        "client_id":  a.client_id,
        "title":      a.title,
        "content":    a.content,
        "summary":    a.summary,
        "category":   a.category or "general",
        "created_at": a.created_at.timestamp() if a.created_at else None,
        "updated_at": a.updated_at.timestamp() if a.updated_at else None,
    }


# ── Summarization (behavior unchanged from the JSON version) ─────────────────
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
    except Exception:
        return "Summary unavailable."


# ── CRUD (all tenant-scoped) ────────────────────────────────────────────────
def add_article(client_id: str, title: str, content: str, category: str = "general") -> dict:
    """Add a new article owned by client_id, with an AI-generated summary."""
    summary = generate_summary(content)
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        article = KnowledgeArticle(
            id=uuid.uuid4().hex,
            client_id=client_id,
            title=title,
            content=content,
            summary=summary,
            category=category or "general",
            created_at=now,
            updated_at=now,
        )
        db.add(article)
        db.commit()
        db.refresh(article)
        return _to_dict(article)
    except Exception as e:
        db.rollback()
        logger.error(f"add_article error (client={client_id[:8]}): {e}")
        raise
    finally:
        db.close()


def get_all_articles(client_id: str) -> list:
    """Return all of this client's articles, most recently updated first."""
    db = SessionLocal()
    try:
        rows = (
            db.query(KnowledgeArticle)
            .filter(KnowledgeArticle.client_id == client_id)
            .order_by(KnowledgeArticle.updated_at.desc())
            .all()
        )
        return [_to_dict(a) for a in rows]
    finally:
        db.close()


def get_article(client_id: str, article_id: str) -> dict | None:
    """Fetch one article, but only if it belongs to this client."""
    db = SessionLocal()
    try:
        a = (
            db.query(KnowledgeArticle)
            .filter(
                KnowledgeArticle.id == article_id,
                KnowledgeArticle.client_id == client_id,
            )
            .first()
        )
        return _to_dict(a) if a else None
    finally:
        db.close()


def update_article(client_id: str, article_id: str, title: str, content: str, category: str) -> dict | None:
    """Update an article and regenerate its summary, only if it belongs to this client."""
    db = SessionLocal()
    try:
        a = (
            db.query(KnowledgeArticle)
            .filter(
                KnowledgeArticle.id == article_id,
                KnowledgeArticle.client_id == client_id,
            )
            .first()
        )
        if not a:
            return None
        a.title = title
        a.content = content
        a.category = category or "general"
        a.summary = generate_summary(content)
        a.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(a)
        return _to_dict(a)
    except Exception as e:
        db.rollback()
        logger.error(f"update_article error (client={client_id[:8]}): {e}")
        raise
    finally:
        db.close()


def delete_article(client_id: str, article_id: str) -> bool:
    """Delete an article, only if it belongs to this client. Returns success."""
    db = SessionLocal()
    try:
        a = (
            db.query(KnowledgeArticle)
            .filter(
                KnowledgeArticle.id == article_id,
                KnowledgeArticle.client_id == client_id,
            )
            .first()
        )
        if not a:
            return False
        db.delete(a)
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"delete_article error (client={client_id[:8]}): {e}")
        raise
    finally:
        db.close()


# ── Retrieval (tenant-scoped; keyword scoring preserved) ────────────────────
def search_kb(client_id: str, query: str, max_results: int = 3) -> list:
    """
    Simple keyword search across title and content, restricted to this client's
    articles. Scoring is identical to the previous JSON version.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(KnowledgeArticle)
            .filter(KnowledgeArticle.client_id == client_id)
            .all()
        )
    finally:
        db.close()

    if not rows:
        return []

    query_words = set(query.lower().split())
    scored = []

    for a in rows:
        score = 0
        title_lower = a.title.lower()
        content_lower = a.content.lower()

        for word in query_words:
            if len(word) < 3:
                continue
            if word in title_lower:
                score += 3  # Title match worth more
            if word in content_lower:
                score += 1

        if score > 0:
            scored.append((score, a))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [_to_dict(a) for _, a in scored[:max_results]]


def get_kb_context(client_id: str, query: str) -> str:
    """
    Build a context string from this client's relevant KB articles for injection
    into Magni's prompt. Returns empty string if no relevant articles found.
    """
    relevant = search_kb(client_id, query)
    if not relevant:
        return ""

    context_parts = ["KNOWLEDGE BASE — Use this information to answer accurately:"]
    for article in relevant:
        context_parts.append(f"\n[{article['title']}]\n{article['content'][:800]}")

    return "\n".join(context_parts)


def get_categories(client_id: str) -> list:
    """Return all unique categories in this client's KB."""
    db = SessionLocal()
    try:
        rows = (
            db.query(KnowledgeArticle.category)
            .filter(KnowledgeArticle.client_id == client_id)
            .distinct()
            .all()
        )
        return sorted({(r[0] or "general") for r in rows})
    finally:
        db.close()
