# One short flow per kind of message. Triage picks the intent; the resolver
# adds the matching flow to its prompt.
SUPPORT_FLOWS = {
    "product": """The visitor is asking about what the business offers or how it works.
Be friendly and quick. Share facts (products, services, prices, hours, shipping, returns,
policies) only when they appear in the knowledge base. If a fact isn't there, say the
team will confirm it.""",

    "account": """The visitor is an existing customer asking about their own order, booking, bill,
or account. You can't see orders, accounts, bookings, or payments, and you can't make
changes, issue refunds, or cancel anything. Explain the relevant policy or next step from
the knowledge base if it has one. If they need someone to look at their specific case,
hand it to the team.""",

    "general": """The visitor's message isn't clearly a question about the business.
If it's a greeting, greet them and ask how you can help. If it's about the business
(hours, location, how to contact the team), answer from the knowledge base. If it's
unrelated to this business, politely say you can help with questions about the business
and offer that.""",
}


def get_flow_context(intent: str) -> str:
    return SUPPORT_FLOWS.get(intent, SUPPORT_FLOWS["general"])
