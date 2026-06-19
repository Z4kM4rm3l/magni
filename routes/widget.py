"""
routes/widget.py

Client-facing widget API. Three endpoints:
  GET  /widget/config     — returns bot config for widget init
  POST /widget/chat       — proxies messages through the orchestrator
  GET  /widget/magni.js   — serves the embeddable JS snippet

CORS is scoped per-request to each client's allowed_domains list.
The operator panel routes (/admin, /api/*) are not affected.
"""
import uuid
from flask import Blueprint, request, jsonify, make_response, current_app
from core.db import SessionLocal
from core.models import Client
from core.utils import sanitize_input, logger
from core.conversation_manager import conversation_manager
from core.conversation_store import start_conversation, add_message_to_conversation
from agents.orchestrator import orchestrator

widget_bp = Blueprint("widget", __name__)


def _get_client_by_key(api_key: str) -> Client | None:
    db = SessionLocal()
    try:
        return db.query(Client).filter(
            Client.api_key == api_key,
            Client.is_active == True,
        ).first()
    finally:
        db.close()


def _cors_headers(client: Client) -> dict:
    """Build CORS headers scoped to the client's allowed_domains list."""
    origin = request.headers.get("Origin", "")
    domains = client.allowed_domains or []

    # Strip protocol from origin for comparison: "https://foo.com" -> "foo.com"
    origin_host = origin.replace("https://", "").replace("http://", "").rstrip("/")

    if origin_host in domains or "*" in domains:
        allowed_origin = origin or "*"
    else:
        allowed_origin = "null"  # blocked — browser will reject

    return {
        "Access-Control-Allow-Origin":  allowed_origin,
        "Access-Control-Allow-Headers": "Content-Type, X-Magni-API-Key",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    }


def _add_cors(response, client: Client):
    for k, v in _cors_headers(client).items():
        response.headers[k] = v
    return response


@widget_bp.route("/widget/config", methods=["GET", "OPTIONS"])
def widget_config():
    api_key = request.args.get("key", "").strip()
    if not api_key:
        return jsonify({"error": "Missing key"}), 400

    client = _get_client_by_key(api_key)
    if not client:
        return jsonify({"error": "Invalid or inactive API key"}), 403

    if request.method == "OPTIONS":
        resp = make_response("", 204)
        return _add_cors(resp, client)

    resp = make_response(jsonify({
        "bot_name":       client.bot_name or "Magni",
        "primary_color":  client.primary_color or "#f59e0b",
        "welcome_message": client.welcome_message or "Hi! How can I help you today?",
    }))
    return _add_cors(resp, client)


@widget_bp.route("/widget/chat", methods=["POST", "OPTIONS"])
def widget_chat():
    api_key = request.headers.get("X-Magni-API-Key", "").strip()
    if not api_key:
        return jsonify({"error": "Missing X-Magni-API-Key header"}), 401

    client = _get_client_by_key(api_key)
    if not client:
        return jsonify({"error": "Invalid or inactive API key"}), 403

    if request.method == "OPTIONS":
        resp = make_response("", 204)
        return _add_cors(resp, client)

    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"error": "No message provided"}), 400

    message = sanitize_input(data.get("message", ""))
    if not message:
        return jsonify({"error": "Empty message"}), 400

    session_id = data.get("session_id") or str(uuid.uuid4())
    history = conversation_manager.get_history(session_id)

    result = orchestrator.run(
        message=message,
        session_id=session_id,
        history=history,
        api_key=api_key,
    )

    response_text = result["response"]
    intent = result.get("intent", "general")

    logger.info(
        f"Widget: client={client.id[:8]} session={session_id[:8]} "
        f"intent={intent} route={result.get('route')}"
    )

    start_conversation(session_id)
    conversation_manager.add_message(session_id, "user", message)
    conversation_manager.add_message(session_id, "assistant", response_text)
    add_message_to_conversation(session_id, "user", message, intent)
    add_message_to_conversation(session_id, "assistant", response_text, intent)

    resp = make_response(jsonify({
        "response":   response_text,
        "session_id": session_id,
    }))
    return _add_cors(resp, client)


@widget_bp.route("/widget/magni.js", methods=["GET"])
def widget_js():
    js = _MAGNI_JS
    resp = make_response(js, 200)
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


# Inline so the blueprint is self-contained — no static file dependency.
_MAGNI_JS = r"""
(function () {
  var script = document.currentScript ||
    document.querySelector('script[data-api-key]');
  if (!script) return;

  var API_KEY = script.getAttribute('data-api-key');
  var BASE_URL = script.src.replace('/widget/magni.js', '');
  var sessionId = 'mgn_' + Math.random().toString(36).slice(2);
  var config = { bot_name: 'Magni', primary_color: '#f59e0b', welcome_message: 'Hi! How can I help you today?' };

  // ── Fetch config ────────────────────────────────────────────────────────
  fetch(BASE_URL + '/widget/config?key=' + encodeURIComponent(API_KEY))
    .then(function (r) { return r.json(); })
    .then(function (data) {
      config = data;
      buildWidget();
    })
    .catch(function () { buildWidget(); });

  // ── Build DOM ────────────────────────────────────────────────────────────
  function buildWidget() {
    var color = config.primary_color || '#f59e0b';

    var style = document.createElement('style');
    style.textContent = [
      '#mgn-bubble{position:fixed;bottom:24px;right:24px;width:56px;height:56px;border-radius:50%;background:' + color + ';cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.25);display:flex;align-items:center;justify-content:center;z-index:99999;}',
      '#mgn-bubble svg{width:28px;height:28px;fill:#fff;}',
      '#mgn-panel{display:none;position:fixed;bottom:92px;right:24px;width:340px;max-height:520px;border-radius:16px;overflow:hidden;box-shadow:0 8px 32px rgba(0,0,0,.18);z-index:99999;flex-direction:column;background:#fff;}',
      '#mgn-panel.open{display:flex;}',
      '#mgn-header{background:' + color + ';padding:14px 16px;color:#fff;font-family:sans-serif;font-weight:600;font-size:15px;}',
      '#mgn-messages{flex:1;overflow-y:auto;padding:12px;font-family:sans-serif;font-size:14px;display:flex;flex-direction:column;gap:8px;}',
      '.mgn-msg{max-width:80%;padding:9px 13px;border-radius:12px;line-height:1.45;}',
      '.mgn-msg.user{align-self:flex-end;background:' + color + ';color:#fff;border-bottom-right-radius:3px;}',
      '.mgn-msg.bot{align-self:flex-start;background:#f3f4f6;color:#111;border-bottom-left-radius:3px;}',
      '#mgn-footer{display:flex;padding:8px;border-top:1px solid #e5e7eb;gap:6px;}',
      '#mgn-input{flex:1;border:1px solid #d1d5db;border-radius:8px;padding:8px 10px;font-size:14px;outline:none;}',
      '#mgn-send{background:' + color + ';border:none;border-radius:8px;padding:8px 14px;color:#fff;cursor:pointer;font-size:14px;}'
    ].join('');
    document.head.appendChild(style);

    var bubble = document.createElement('div');
    bubble.id = 'mgn-bubble';
    bubble.innerHTML = '<svg viewBox="0 0 24 24"><path d="M12 2C6.477 2 2 6.253 2 11.5c0 2.304.878 4.41 2.326 6.02L3 22l4.712-1.38C9.1 21.5 10.52 22 12 22c5.523 0 10-4.253 10-9.5S17.523 2 12 2z"/></svg>';

    var panel = document.createElement('div');
    panel.id = 'mgn-panel';
    panel.innerHTML =
      '<div id="mgn-header">' + (config.bot_name || 'Magni') + '</div>' +
      '<div id="mgn-messages"></div>' +
      '<div id="mgn-footer"><input id="mgn-input" type="text" placeholder="Type a message..."/><button id="mgn-send">Send</button></div>';

    document.body.appendChild(bubble);
    document.body.appendChild(panel);

    var messages = panel.querySelector('#mgn-messages');
    var input = panel.querySelector('#mgn-input');
    var open = false;

    // Welcome message
    appendMsg(config.welcome_message || 'Hi! How can I help you today?', 'bot');

    bubble.addEventListener('click', function () {
      open = !open;
      panel.classList.toggle('open', open);
      if (open) input.focus();
    });

    panel.querySelector('#mgn-send').addEventListener('click', sendMessage);
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
  }

  // ── Messaging ────────────────────────────────────────────────────────────
  function appendMsg(text, role) {
    var messages = document.getElementById('mgn-messages');
    if (!messages) return;
    var el = document.createElement('div');
    el.className = 'mgn-msg ' + role;
    el.textContent = text;
    messages.appendChild(el);
    messages.scrollTop = messages.scrollHeight;
  }

  function sendMessage() {
    var input = document.getElementById('mgn-input');
    var text = (input.value || '').trim();
    if (!text) return;
    input.value = '';
    appendMsg(text, 'user');

    fetch(BASE_URL + '/widget/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Magni-API-Key': API_KEY },
      body: JSON.stringify({ message: text, session_id: sessionId })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) { appendMsg(data.response || 'Sorry, something went wrong.', 'bot'); })
      .catch(function () { appendMsg('Connection error. Please try again.', 'bot'); });
  }
})();
""".strip()
