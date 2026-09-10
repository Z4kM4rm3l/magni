# Magni

## Multi-Agent Customer Support SaaS Platform

Magni is a multi-tenant AI customer support platform designed to automate customer conversations while maintaining control over **routing, knowledge grounding, human escalation, tenant isolation, and AI costs**.

Rather than relying on a single general-purpose chatbot, Magni uses a coordinated team of specialized AI agents. Conversations are triaged, routed, resolved, or escalated through an explicit orchestration layer, making the system easier to reason about, test, and debug.

Magni is the flagship product of **Lemram Industries**.

**Role:** Solo Architect & AI Software Engineer
**Status:** Deployed and pilot-ready
**Stack:** Python, Flask, PostgreSQL, SQLAlchemy, Google Gemini 2.5, Stripe, Railway

---

## What Magni Does

Magni provides businesses with an AI-powered customer support layer that can:

* Answer customer questions using tenant-specific knowledge
* Route conversations according to intent
* Escalate conversations requiring human intervention
* Maintain conversation state across interactions
* Provide an embeddable customer-facing support widget
* Track AI usage and enforce account limits
* Support authenticated tenant accounts
* Manage subscription and billing workflows
* Provision isolated demo environments
* Send escalation notifications to human support staff

The system was designed around a simple principle:

> **AI should automate the work, not remove operational control.**

That principle influenced the architecture, billing safeguards, escalation system, and knowledge-grounding strategy.

---

## Architecture

```text
                    ┌──────────────────┐
                    │    Customer      │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │   Chat Widget    │
                    └────────┬─────────┘
                             │
                             ▼
                 ┌────────────────────────┐
                 │   Orchestrator Agent   │
                 │                        │
                 │ State + Routing + Flow │
                 └───────────┬────────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
       ┌────────────┐ ┌────────────┐ ┌────────────┐
       │   Triage   │ │  Resolver  │ │ Escalation │
       │   Agent    │ │   Agent    │ │   Agent    │
       └─────┬──────┘ └─────┬──────┘ └─────┬──────┘
             │              │              │
             └──────────────┼──────────────┘
                            │
                            ▼
                 ┌────────────────────┐
                 │ Tenant Knowledge   │
                 │ Base               │
                 └─────────┬──────────┘
                           │
                           ▼
                 ┌────────────────────┐
                 │ Application Data   │
                 │                    │
                 │ Conversations      │
                 │ Knowledge          │
                 │ Usage / Billing    │
                 │ Tenant State       │
                 └────────────────────┘
```

### Agent Responsibilities

**Orchestrator**

Owns conversation state and coordinates the overall workflow, determining which specialized agent should handle the next step.

**Triage Agent**

Classifies customer intent and determines the appropriate workflow.

**Resolver Agent**

Generates responses using relevant tenant knowledge retrieved from the application's knowledge base.

**Escalation Agent**

Packages relevant conversation context and routes unresolved or sensitive issues toward human support.

The separation is intentional. Each agent has a narrower responsibility, making behavior easier to inspect, test, and modify.

---

## Why Multiple Agents?

A single LLM call can produce a convincing customer-support response, but convincing output is not the same thing as a reliable support system.

Magni separates responsibilities so that:

* Routing logic is explicit
* Agents have narrower responsibilities
* Knowledge retrieval is separated from escalation decisions
* Failures are easier to isolate
* Individual agents can be tested independently
* New workflows can be added without turning one prompt into a monolith

The orchestrator provides the control layer while specialized agents handle individual pieces of the workflow.

---

## Tenant Isolation

Magni is designed as a multi-tenant SaaS application, meaning customer data must remain isolated even though tenants share application infrastructure.

Tenant-aware data access applies across:

* Conversations
* Knowledge-base content
* Authentication
* Usage tracking
* Billing information
* Account configuration

Retrieved knowledge is also associated with the relevant tenant before being supplied to the resolver workflow.

---

## Knowledge-Grounded Responses

Magni currently uses a lightweight, application-level retrieval approach rather than a vector database.

Knowledge-base articles are stored as structured data and searched using keyword matching across article titles and content. Relevant results are assembled into context for the Resolver Agent.

```text
Customer Question
       │
       ▼
Keyword Retrieval
       │
       ▼
Relevant Tenant Knowledge
       │
       ▼
Resolver Agent
       │
       ▼
Grounded Response
```

This approach was intentionally kept simple for the current scale of the application. It provides a clear retrieval path without introducing the operational complexity of a dedicated vector database.

At larger knowledge-base volumes, semantic retrieval and vector storage would be a natural next step.

---

## Operational Safety and AI Cost Controls

LLM applications introduce a unique operational risk: a software bug, retry loop, unexpected traffic spike, or runaway workflow can translate directly into API costs.

Magni addresses this with multiple independent layers of protection:

### Atomic Database Usage Controls

Database-level locking is used when updating usage counters so concurrent requests cannot incorrectly bypass account limits.

### Fail-Closed Exception Handling

When usage-control operations encounter an unexpected failure, the system defaults toward preventing additional model usage rather than allowing requests to continue unchecked.

### Global Application Usage Cap

An additional application-level safeguard provides a broader ceiling against runaway model usage.

### Infrastructure Spending Limit

Cloud-level spending protection provides a final boundary outside the application itself.

The layers are intentionally redundant. A failure in one protection mechanism should not automatically become an unrestricted LLM billing event.

---

## Billing & Account Management

Magni integrates subscription billing through Stripe while maintaining application-side usage controls.

The application includes:

* JWT-based authentication
* Password hashing with bcrypt
* Tenant-aware authorization
* Account management
* Demo account provisioning
* Subscription-aware application behavior
* Application-level usage enforcement

Authentication and billing boundaries remain separate from the AI orchestration layer so the model is not responsible for application security decisions.

---

## Embeddable Support Widget

The customer-facing interface is designed to be embedded into a business website rather than requiring customers to visit a separate application.

```text
Business Website
       │
       └── Embedded Magni Widget
                    │
                    ▼
                 Magni API
                    │
                    ▼
           Agent Orchestration
```

This allows Magni to function as a support layer within an existing website.

---

## Engineering Decisions

### Explicit Agent Boundaries

Instead of placing every responsibility into one large prompt, agent responsibilities are separated into explicit components.

**Reason:** Smaller responsibilities are easier to debug, test, and evolve.

### Database-Enforced Usage Controls

Usage limits are not implemented purely in application memory.

**Reason:** Concurrent requests can otherwise create race conditions where multiple requests observe the same remaining allowance.

### Redundant Cost Protection

Application-level safeguards are backed by infrastructure-level spending limits.

**Reason:** No single software safeguard should be treated as an absolute guarantee against runaway usage.

### Tenant-Scoped Knowledge Retrieval

Knowledge retrieval is scoped to the relevant tenant.

**Reason:** AI applications introduce a data-isolation problem in addition to the normal database authorization problem.

### Separate Scheduled Services

Scheduled maintenance is separated from the primary web application.

**Reason:** Keeping scheduled work independent makes deployment responsibilities clearer and avoids coupling maintenance tasks to the web process.

---

## Testing

Magni includes automated tests covering the primary agent workflow.

Current coverage includes:

* Orchestrator behavior
* Triage agent behavior
* Resolver agent behavior
* Escalation agent behavior

Tests are located in the `tests/` directory.

The intent is to treat the agent layer as application logic that can be tested rather than relying exclusively on manual inspection of model responses.

---

## Project Structure

```text
magni/
├── agents/
├── core/
├── data/
├── docs/
├── routes/
├── scripts/
├── static/
├── templates/
├── tests/
├── app.py
├── Procfile
├── railway.json
├── railway.cron.json
└── requirements.txt
```

The repository is organized around application responsibilities, keeping the AI agents, core services, routes, frontend assets, data, deployment configuration, and tests clearly separated.

---

## What I Would Change at 10x Scale

Magni is currently designed as a pilot-ready SaaS platform. If usage increased substantially, I would evolve the architecture around the following pressure points.

### Distributed Rate Limiting

Application-level limits would evolve toward distributed rate limiting backed by shared infrastructure so protections remain consistent across multiple application instances.

### Background Job Processing

Long-running work such as knowledge ingestion, notifications, analytics, and maintenance would move toward a dedicated background job system.

### Semantic Retrieval

The current keyword-based retrieval system would be replaced or supplemented with semantic retrieval as knowledge-base size and query complexity increased.

Depending on workload characteristics, PostgreSQL vector capabilities or a dedicated vector database could provide the next step.

### Queue-Based Agent Execution

Long-running or asynchronous agent workflows could move behind a queue to improve reliability and prevent expensive model calls from tying up web workers.

### Observability

I would introduce centralized:

* Structured logging
* Metrics
* Distributed tracing
* Agent-level latency measurements
* Model usage analytics
* Failure monitoring
* Cost-per-conversation tracking

### Stronger Model Evaluation

At larger scale, I would introduce an evaluation suite covering:

* Routing accuracy
* Retrieval quality
* Response grounding
* Escalation accuracy
* Prompt regressions
* Latency
* Cost per conversation

The goal would be to treat model behavior as measurable application behavior rather than something evaluated manually after deployment.

---

## Technology Stack

| Layer           | Technology                |
| --------------- | ------------------------- |
| Backend         | Python, Flask             |
| Database        | PostgreSQL                |
| ORM             | SQLAlchemy                |
| AI              | Google Gemini 2.5         |
| Authentication  | JWT, bcrypt               |
| Billing         | Stripe                    |
| Frontend        | HTML, CSS, JavaScript     |
| Deployment      | Railway                   |
| AI Architecture | Multi-Agent Orchestration |
| Knowledge       | Keyword-Based Retrieval   |
| Scheduled Tasks | Railway Cron              |

---

## Development Approach

Magni was built end to end as a solo project.

AI coding agents were used as development tools throughout implementation, but architectural decisions, system design, debugging, integration, and validation remained under my direction.

The project was intentionally built beyond the scope of a simple chatbot demo.

The goal was to explore the engineering problems that appear when an LLM becomes part of an actual software system:

* How should agents coordinate?
* How should application state be maintained?
* How should tenant data be isolated?
* What happens when concurrent requests modify usage?
* How should human escalation work?
* How should AI costs be bounded?
* How should AI behavior be tested?
* What architectural changes become necessary as usage grows?

---

## Current Status

**Deployed and pilot-ready.**

Magni demonstrates a complete SaaS architecture around AI customer support, including authentication, tenant isolation, knowledge grounding, multi-agent orchestration, billing, usage controls, escalation workflows, automated testing, and deployment infrastructure.

It is currently positioned as a portfolio and pilot project rather than a production system with an active customer base.

---

## Future Improvements

Potential future development includes:

* Semantic knowledge retrieval
* Expanded agent evaluation framework
* Additional model providers
* Human support dashboards
* Agent performance analytics
* Conversation analytics
* Distributed background processing
* Expanded billing tiers
* Automated knowledge-base ingestion
* Production observability stack

---

## About

**Zakary Marmel**
Solo Architect & AI Software Engineer

I build AI-powered software systems that combine LLM capabilities with traditional application engineering.

My background in professional operations influences how I approach these systems: the goal is not simply to make an AI model produce impressive output, but to build software that fits into real workflows, has clear failure boundaries, and remains understandable when something goes wrong.

**GitHub:** [Z4kM4rm3l](https://github.com/Z4kM4rm3l)
**Portfolio:** [zakarymarmel.netlify.app](https://zakarymarmel.netlify.app)

