import os
import uuid
from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from flask_cors import CORS
from dotenv import load_dotenv

from core.api_client import get_magni_response
from core.intent_classifier import classify_intent
from core.conversation_manager import conversation_manager
from core.conversation_store import (
    start_conversation, add_message_to_conversation,
    set_resolution, set_rating, get_all_conversations,
    get_analytics_data
)
from core.feedback_flow import save_feedback, get_feedback_summary
from core.knowledge_base import (
    add_article, get_all_articles, get_article,
    update_article, delete_article, get_categories
)
from core.client_manager import (
    create_client, get_all_clients, get_client,
    update_client, suspend_client, reactivate_client,
    delete_client, get_client_stats
)
from core.auth import login_required, check_credentials
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

    # Ensure conversation record exists
    start_conversation(session_id)

    response = get_magni_response(message, history, intent)

    # Store in memory for context
    conversation_manager.add_message(session_id, "user", message)
    conversation_manager.add_message(session_id, "assistant", response)

    # Persist to conversation store
    add_message_to_conversation(session_id, "user", message, intent)
    add_message_to_conversation(session_id, "assistant", response, intent)

    return jsonify({
        "response": response,
        "intent": intent,
        "session_id": session_id
    })

@app.route("/resolve", methods=["POST"])
def resolve():
    """Customer clicks Yes/No resolution button."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400

    session_id = data.get("session_id")
    resolved = data.get("resolved")

    if session_id is None or resolved is None:
        return jsonify({"error": "Missing fields"}), 400

    set_resolution(session_id, bool(resolved))
    return jsonify({"status": "ok"})

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

    # Save to both feedback store and conversation store
    save_feedback(session_id, rating, comment)
    set_rating(session_id, rating, comment)

    return jsonify({"status": "Thank you for your feedback!"})

@app.route("/reset", methods=["POST"])
def reset():
    data = request.get_json()
    session_id = data.get("session_id") if data else None
    if session_id:
        conversation_manager.reset_session(session_id)
    return jsonify({"status": "Session reset"})

@app.route("/health")
def health():
    return jsonify({"status": "ok", "agent": "Magni"})

# ── AUTH ROUTES ──────────────────────────────────────────────

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if check_credentials(username, password):
            session["admin_logged_in"] = True
            next_url = request.args.get("next", "/admin")
            return redirect(next_url)
        return render_template("login.html", error="Invalid username or password.")
    return render_template("login.html", error=None)

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect("/admin/login")

# ── ADMIN HUB ────────────────────────────────────────────────

@app.route("/admin")
@login_required
def admin_hub():
    return render_template("hub.html")

@app.route("/admin/kb")
@login_required
def admin_kb():
    return render_template("admin.html")

@app.route("/admin/clients")
@login_required
def admin_clients():
    return render_template("clients.html")

@app.route("/admin/analytics")
@login_required
def admin_analytics():
    return render_template("analytics.html")

# ── KNOWLEDGE BASE ROUTES ─────────────────────────────────────

@app.route("/api/kb", methods=["GET"])
@login_required
def kb_list():
    articles = get_all_articles()
    return jsonify({"articles": articles, "categories": get_categories()})

@app.route("/api/kb", methods=["POST"])
@login_required
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
@login_required
def kb_get(article_id):
    article = get_article(article_id)
    if not article:
        return jsonify({"error": "Article not found"}), 404
    return jsonify({"article": article})

@app.route("/api/kb/<article_id>", methods=["PUT"])
@login_required
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
    return jsonify({"article": article})

@app.route("/api/kb/<article_id>", methods=["DELETE"])
@login_required
def kb_delete(article_id):
    success = delete_article(article_id)
    if not success:
        return jsonify({"error": "Article not found"}), 404
    return jsonify({"status": "deleted"})

# ── CLIENT MANAGEMENT ROUTES ──────────────────────────────────

@app.route("/api/clients", methods=["GET"])
@login_required
def clients_list():
    clients = get_all_clients()
    stats = get_client_stats()
    return jsonify({"clients": clients, "stats": stats})

@app.route("/api/clients", methods=["POST"])
@login_required
def clients_create():
    data = request.get_json()
    if not data or not data.get("business_name") or not data.get("email"):
        return jsonify({"error": "Business name and email are required"}), 400

    client = create_client(
        business_name=sanitize_input(data["business_name"]),
        email=sanitize_input(data["email"]),
        tier=data.get("tier", "starter"),
        bot_name=sanitize_input(data.get("bot_name", "Magni")),
        api_key=data.get("api_key", ""),
        billing_date=data.get("billing_date", "")
    )

    if data.get("notes"):
        update_client(client["client_id"], notes=sanitize_input(data["notes"]))

    logger.info(f"New client: {client['business_name']} ({client['tier']})")
    return jsonify({"client": client}), 201

@app.route("/api/clients/<client_id>", methods=["PUT"])
@login_required
def clients_update(client_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    updates = {}
    for field in ["business_name", "email", "bot_name", "billing_date", "notes"]:
        if field in data:
            updates[field] = sanitize_input(data[field])
    if "tier" in data:
        updates["tier"] = data["tier"]

    client = update_client(client_id, **updates)
    if not client:
        return jsonify({"error": "Client not found"}), 404
    return jsonify({"client": client})

@app.route("/api/clients/<client_id>/suspend", methods=["POST"])
@login_required
def clients_suspend(client_id):
    client = suspend_client(client_id)
    if not client:
        return jsonify({"error": "Client not found"}), 404
    return jsonify({"client": client})

@app.route("/api/clients/<client_id>/activate", methods=["POST"])
@login_required
def clients_activate(client_id):
    client = reactivate_client(client_id)
    if not client:
        return jsonify({"error": "Client not found"}), 404
    return jsonify({"client": client})

@app.route("/api/clients/<client_id>", methods=["DELETE"])
@login_required
def clients_delete(client_id):
    success = delete_client(client_id)
    if not success:
        return jsonify({"error": "Client not found"}), 404
    return jsonify({"status": "deleted"})

# ── ANALYTICS ROUTES ──────────────────────────────────────────

@app.route("/api/insights", methods=["POST"])
@login_required
def ai_insights():
    """Generate AI-powered insights from analytics data."""
    import google.generativeai as genai
    data = request.get_json()
    prompt = data.get("prompt", "")
    if not prompt:
        return jsonify({"insights": []}), 400
    try:
        model = genai.GenerativeModel(model_name="gemini-2.5-flash")
        response = model.generate_content(prompt)
        text = response.text.strip()
        # Strip markdown fences if present
        text = text.replace("```json", "").replace("```", "").strip()
        import json as _json
        insights = _json.loads(text)
        return jsonify({"insights": insights})
    except Exception as e:
        logger.error(f"Insights error: {e}")
        return jsonify({"insights": []}), 500

@app.route("/api/analytics", methods=["GET"])
@login_required
def analytics_data():
    data = get_analytics_data()
    return jsonify(data)

@app.route("/api/analytics/conversations", methods=["GET"])
@login_required
def analytics_conversations():
    conversations = get_all_conversations()
    # Return last 50, newest first, without full message content
    recent = sorted(conversations,
                   key=lambda x: x.get("started_at", 0), reverse=True)[:50]
    summary = [{
        "session_id": c["session_id"][:8],
        "date": c.get("date", ""),
        "message_count": c.get("message_count", 0),
        "primary_intent": c.get("primary_intent", "general"),
        "resolved": c.get("resolved"),
        "rating": c.get("rating"),
        "duration": round((c.get("ended_at", 0) or 0) -
                         (c.get("started_at", 0) or 0))
    } for c in recent]
    return jsonify({"conversations": summary})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
