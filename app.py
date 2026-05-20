import os
import uuid
from flask import Flask, request, jsonify, render_template, session
from flask_cors import CORS
from dotenv import load_dotenv

from core.api_client import get_magni_response
from core.intent_classifier import classify_intent
from core.conversation_manager import conversation_manager
from core.feedback_flow import save_feedback, get_feedback_summary
from core.knowledge_base import (
    add_article, get_all_articles, get_article,
    update_article, delete_article, get_categories
)
from core.utils import sanitize_input, logger

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "magni-dev-secret")
CORS(app)

# ── CHAT ROUTES ──────────────────────────────────────────────

@app.route("/")
def index():
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())
    return render_template("chat.html")

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"error": "No message provided"}), 400

    message = sanitize_input(data.get("message", ""))
    if not message:
        return jsonify({"error": "Empty message"}), 400

    session_id = data.get("session_id") or session.get("session_id", str(uuid.uuid4()))
    intent = classify_intent(message)
    history = conversation_manager.get_history(session_id)

    logger.info(f"Session: {session_id[:8]} | Intent: {intent} | Message: {message[:50]}")

    response = get_magni_response(message, history, intent)

    conversation_manager.add_message(session_id, "user", message)
    conversation_manager.add_message(session_id, "assistant", response)

    return jsonify({
        "response": response,
        "intent": intent,
        "session_id": session_id
    })

@app.route("/feedback", methods=["POST"])
def feedback():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    session_id = data.get("session_id", "unknown")
    rating = data.get("rating", 0)
    comment = sanitize_input(data.get("comment", ""))

    if not isinstance(rating, int) or rating < 1 or rating > 5:
        return jsonify({"error": "Rating must be 1-5"}), 400

    success = save_feedback(session_id, rating, comment)
    if success:
        return jsonify({"status": "Thank you for your feedback!"})
    return jsonify({"error": "Could not save feedback"}), 500

@app.route("/reset", methods=["POST"])
def reset():
    data = request.get_json()
    session_id = data.get("session_id") if data else None
    if session_id:
        conversation_manager.reset_session(session_id)
    return jsonify({"status": "Session reset"})

@app.route("/health")
def health():
    summary = get_feedback_summary()
    return jsonify({"status": "ok", "agent": "Magni", "feedback": summary})

# ── ADMIN / KNOWLEDGE BASE ROUTES ────────────────────────────

@app.route("/admin")
def admin():
    return render_template("admin.html")

@app.route("/api/kb", methods=["GET"])
def kb_list():
    articles = get_all_articles()
    return jsonify({"articles": articles, "categories": get_categories()})

@app.route("/api/kb", methods=["POST"])
def kb_create():
    data = request.get_json()
    if not data or not data.get("title") or not data.get("content"):
        return jsonify({"error": "Title and content are required"}), 400

    title = sanitize_input(data["title"])
    content = data["content"].strip()
    category = sanitize_input(data.get("category", "general"))

    if len(content) < 10:
        return jsonify({"error": "Content too short"}), 400

    article = add_article(title, content, category)
    logger.info(f"KB article added: {title}")
    return jsonify({"article": article}), 201

@app.route("/api/kb/<article_id>", methods=["GET"])
def kb_get(article_id):
    article = get_article(article_id)
    if not article:
        return jsonify({"error": "Article not found"}), 404
    return jsonify({"article": article})

@app.route("/api/kb/<article_id>", methods=["PUT"])
def kb_update(article_id):
    data = request.get_json()
    if not data or not data.get("title") or not data.get("content"):
        return jsonify({"error": "Title and content are required"}), 400

    article = update_article(
        article_id,
        sanitize_input(data["title"]),
        data["content"].strip(),
        sanitize_input(data.get("category", "general"))
    )

    if not article:
        return jsonify({"error": "Article not found"}), 404

    logger.info(f"KB article updated: {article_id}")
    return jsonify({"article": article})

@app.route("/api/kb/<article_id>", methods=["DELETE"])
def kb_delete(article_id):
    success = delete_article(article_id)
    if not success:
        return jsonify({"error": "Article not found"}), 404
    logger.info(f"KB article deleted: {article_id}")
    return jsonify({"status": "deleted"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
