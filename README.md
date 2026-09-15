# Magni

## Multi-Tenant AI Customer Operations Platform

Magni is a multi-tenant AI customer support and customer-operations platform designed to help businesses automate customer conversations while preserving control over routing, knowledge grounding, escalation, tenant isolation, usage, and AI costs.

Rather than relying on a single general-purpose chatbot, Magni uses a coordinated set of specialized agents behind an orchestration layer. Customer requests can be classified, answered using tenant-specific knowledge, escalated to a human, and persisted for later analytics.

Magni is the flagship product of **Lemram Industries**.

**Role:** Solo Architect & AI Software Engineer  
**Status:** Pilot-readiness hardening in progress  
**Stack:** Python, Flask, PostgreSQL, SQLAlchemy, Alembic, Google Gemini 2.5, Stripe, Railway

---

## Product Vision

Magni is being built as an AI intelligence layer that can eventually sit between a business's customers and the tools the business already uses.

The goal is not to replace CRMs, scheduling platforms, email providers, or other operational systems. Instead, Magni is intended to understand customer requests, determine what should happen next, use the appropriate business tools, and escalate when human judgment is required.

Today, the implemented product focuses on:

- Embeddable website chat
- Tenant-specific knowledge
- Multi-agent routing and resolution
- Human escalation
- Tenant accounts and self-service onboarding
- Conversation persistence
- Analytics foundations
- Usage and billing controls

Future channel and integration work is intended to extend the same intelligence layer across voice, email, messaging, scheduling, and external business systems.

---

## Current Customer Flow

Magni now supports a self-service tenant lifecycle:

```text
Business Owner
      │
      ▼
Create Account
      │
      ▼
Tenant Provisioned
      │
      ▼
Authenticated Portal
      │
      ├── Account / Plan Information
      ├── Widget Credentials
      ├── Embed Snippet
      └── Tenant Configuration
      │
      ▼
Install Magni Widget
      │
      ▼
Customer Conversations
      │
      ▼
Tenant-Scoped Persistence

The objective of the current development phase is to complete the remaining controls required for a business to operate Magni without manual database intervention.

What Magni Does

Magni currently provides:

Tenant account signup and authentication
Automatic tenant provisioning
Authenticated customer portal
Embeddable website support widget
Tenant-specific knowledge bases
Intent classification and routing
AI-generated responses grounded in tenant knowledge
Human escalation workflows
PostgreSQL-backed conversation persistence
Tenant-scoped conversation analytics
Usage tracking and account limits
Subscription and billing infrastructure
Demo account isolation
Email escalation notifications
Layered AI cost protection

The system is built around a simple principle:

AI should automate work without removing operational control.

That principle affects how Magni handles routing, escalation, tenancy, cost controls, and future tool integrations.

Architecture
                    ┌──────────────────┐
                    │     Customer     │
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
                  │ PostgreSQL         │
                  │                    │
                  │ Clients            │
                  │ Knowledge          │
                  │ Conversations      │
                  │ Messages           │
                  │ Usage / Billing    │
                  │ Tenant State       │
                  └────────────────────┘
Agent Responsibilities
Orchestrator

Coordinates the overall workflow, maintains routing state, and determines which specialized agent should handle the next step.

The orchestrator also carries resolved tenant identity through the agent workflow so downstream operations can remain tenant-aware.

Triage Agent

Classifies the customer's intent and determines the appropriate workflow.

Resolver Agent

Generates answers using relevant knowledge retrieved from the current tenant's knowledge base.

Escalation Agent

Packages relevant context and routes unresolved or sensitive requests toward human support.

The separation is intentional. Each agent has a narrower responsibility, which makes behavior easier to inspect, test, and evolve.

Why Multiple Agents?

A single LLM call can produce a convincing customer-service response, but convincing output is not the same as a reliable support system.

Magni separates responsibilities so that:

Routing logic remains explicit
Individual agents have narrow responsibilities
Retrieval is separated from escalation decisions
Failures are easier to isolate
Components can be tested independently
Future workflows can be introduced without turning one prompt into a monolith

The orchestrator acts as the control layer while specialized agents handle specific responsibilities.

Multi-Tenant Architecture

Tenant isolation is a core architectural requirement rather than an application-level convention.

Tenant-aware data access applies across:

Client accounts
Knowledge articles
Conversations
Messages
Authentication
Analytics
Usage tracking
Billing state
Account configuration
Knowledge Isolation

Knowledge articles are stored in PostgreSQL and associated with a required client_id.

Knowledge operations are scoped to both the tenant and the requested resource, preventing one tenant from retrieving or modifying another tenant's knowledge.

Client
  │
  └── KnowledgeArticle
          client_id FK
Conversation Isolation

Durable conversations are also stored in PostgreSQL and owned by a tenant.

Client
  │
  └── Conversation
          │
          └── Message

Conversation ownership is enforced using the pair:

client_id + session_id

rather than trusting a session identifier by itself.

Live in-memory conversation context is also namespaced by tenant so identical session identifiers cannot share context across customers.

Browser widget sessions are server-issued rather than generated entirely by client-side JavaScript.

Conversation Persistence

Magni originally used file-backed conversation persistence during early development.

That storage layer has since been replaced with PostgreSQL-backed models for:

Conversations
Messages
Resolution telemetry
Ratings
Intent history
Analytics data

This eliminates the previous load-modify-rewrite JSON persistence model and provides proper relational ownership and cascade behavior.

Database Migrations

Magni uses Alembic for schema evolution.

The current migration history includes the transition from the original client schema into tenant-scoped knowledge and conversation storage.

0001  Client baseline
  │
0002  Tenant-scoped knowledge articles
  │
0003  Conversations and messages

Application schema changes are therefore represented as explicit migrations rather than relying on create_all() as a production schema-management mechanism.

Knowledge-Grounded Responses

Magni currently uses a lightweight application-level retrieval strategy rather than a vector database.

Knowledge articles are stored in PostgreSQL and searched within the current tenant using keyword matching across article titles and content.

Customer Question
       │
       ▼
Tenant-Scoped Retrieval
       │
       ▼
Relevant Knowledge
       │
       ▼
Resolver Agent
       │
       ▼
Grounded Response

This approach is intentionally simple for the current product scale.

Semantic retrieval or PostgreSQL vector capabilities can be introduced later if knowledge volume and retrieval complexity justify them.

Tenant Signup and Portal

Magni now includes a full tenant signup-to-portal flow.

A new business can:

Create an account
Receive its own tenant record
Authenticate into a tenant-specific session
Enter its customer portal
Receive its widget credential
Copy the website embed snippet
Operate within its own tenant boundary

Client authentication and operator/admin authentication are kept separate so administrative capabilities are not implicitly granted to customer accounts.

The portal is being expanded into the primary self-service control surface for:

Business settings
Knowledge management
Analytics
Widget configuration
Usage visibility
Embeddable Support Widget

The customer-facing interface is designed to live directly on a business website.

Business Website
       │
       └── Magni Widget
               │
               ▼
          Magni API
               │
               ▼
       Agent Orchestration
               │
               ▼
       Tenant Data + Tools

The widget identifies the business tenant and routes conversations through the same orchestration layer used by the rest of the application.

The browser-facing widget identifier should be treated as a publishable widget credential, not as a private server secret.

Future server-to-server integrations can use a separate class of private credentials.

Authentication and Authorization

Magni separates customer identity, tenant identity, and administrative access.

Current authentication architecture includes:

Password hashing
Tenant login sessions
Separate operator/admin authentication
Tenant-aware authorization
Session expiration
Active-account checks

The current pilot-readiness phase is also hardening:

Database-enforced normalized email uniqueness
Session rotation on authentication
Signup/login abuse protection
Tenant ownership regression tests
Operational Safety and AI Cost Controls

LLM applications introduce an unusual operational risk: a retry loop, bug, unexpected traffic spike, or runaway workflow can translate directly into provider costs.

Magni therefore uses several independent layers of protection.

Atomic Tenant Usage Controls

Usage counters are updated using database-level locking so concurrent requests cannot incorrectly bypass account limits.

Fail-Closed Usage Handling

When usage-control operations encounter unexpected failures, the application favors blocking additional model calls rather than allowing unlimited usage.

Application-Level Safety Cap

An additional application-level limit provides protection against unexpected aggregate traffic.

At the current single-instance scale this mechanism is intentionally simple.

Provider / Infrastructure Spend Protection

Provider-level spending limits provide a final boundary outside the application.

These layers are intentionally redundant.

Tenant Limit
     │
     ▼
Database Enforcement
     │
     ▼
Application Safety Cap
     │
     ▼
Provider Spending Limit

No single safeguard is treated as absolute protection.

Billing

Magni includes Stripe-based subscription infrastructure alongside application-level usage controls.

Billing responsibilities are intentionally separate from agent reasoning.

The model does not decide whether a tenant is allowed to consume additional paid resources.

That decision remains application logic.

Current billing-related functionality includes:

Subscription state
Tenant tiers
Monthly usage tracking
Account limits
Stripe customer/subscription identifiers
Payment lifecycle infrastructure
Testing

Magni treats AI orchestration and tenant isolation as application behavior that should be tested rather than trusted through manual inspection alone.

Current testing covers areas including:

Orchestrator behavior
Triage behavior
Resolver behavior
Escalation behavior
Conversation ownership
Cross-tenant mutation prevention
Tenant-namespaced in-memory conversation context

The pilot-readiness test suite is being expanded to cover:

Cross-tenant knowledge isolation
Cross-tenant analytics isolation
Portal authorization
Client versus operator authentication boundaries
Cascade deletion behavior
Inactive tenant behavior
Authentication invariants

Tests are located in the tests/ directory.

Engineering Decisions
Explicit Agent Boundaries

Agent responsibilities are separated instead of combining every task into one large prompt.

Why: smaller responsibilities are easier to debug, test, and evolve.

Tenant + Resource Ownership

Tenant resources are addressed using the owning tenant together with the resource identifier.

For conversations, that means ownership checks use:

client_id + session_id

rather than trusting a session ID alone.

Why: a globally unique identifier is not a substitute for authorization.

Database-Enforced Usage Controls

Usage limits are not implemented purely in application memory.

Why: concurrent requests could otherwise observe the same remaining allowance and exceed a tenant's configured limit.

Server-Issued Conversation Sessions

Initial widget session IDs are generated by the server.

Why: browser-generated identifiers should not be relied on as an ownership boundary.

Tenant-Scoped Knowledge Retrieval

Knowledge retrieval is scoped to the active tenant before context is supplied to the Resolver Agent.

Why: LLM applications introduce a context-isolation problem in addition to ordinary database authorization concerns.

Explicit Database Migrations

Schema evolution is managed with Alembic.

Why: production databases need reproducible schema changes rather than implicit table creation.

Separate Scheduled Services

Scheduled maintenance is kept separate from the primary web application.

Why: deployment responsibilities remain clearer and scheduled tasks do not depend on the lifecycle of a web worker.

Project Structure
magni/
├── agents/
│   ├── orchestrator.py
│   ├── triage_agent.py
│   ├── resolver_agent.py
│   └── escalation_agent.py
│
├── core/
│   ├── db.py
│   ├── models.py
│   ├── knowledge_base.py
│   ├── conversation_store.py
│   ├── conversation_manager.py
│   ├── client_manager.py
│   ├── client_guard.py
│   └── ...
│
├── migrations/
│   └── versions/
│
├── routes/
├── scripts/
├── static/
├── templates/
├── tests/
├── app.py
├── alembic.ini
├── Procfile
├── railway.json
├── railway.cron.json
└── requirements.txt

The repository is organized by application responsibility so AI orchestration, persistence, tenant services, routes, presentation, deployment, and tests remain distinguishable.

Current Development Phase: Pilot-Ready

Magni's core SaaS foundation is now in place:

Multi-agent orchestration
PostgreSQL-backed tenant model
Tenant-scoped knowledge
Tenant-scoped conversations
Relational message persistence
Alembic migrations
Tenant signup
Tenant login
Customer portal
Widget provisioning
Usage controls
Billing infrastructure
Human escalation
Conversation ownership hardening

The current phase is focused on making that foundation safe and self-service enough for an external pilot.

Remaining Pilot-Readiness Work
1. Conversation Ownership Hardening

Status: Implemented / validation in progress

Tenant + session ownership checks
Server-issued widget sessions
Tenant-namespaced live context
Cross-tenant mutation prevention
Regression tests
2. Tenant Settings

Planned customer-facing controls include:

Business name
Bot name
Welcome message
Primary color
Allowed website domains
Widget configuration

The browser-facing widget credential will also be renamed to reflect that it is publishable rather than a private API secret.

3. Integration Test Suite

The next test expansion focuses on tenant and authentication invariants rather than only individual agent behavior.

4. Authentication Correctness

Remaining low-cost hardening includes:

Database-enforced email uniqueness
Session rotation after authentication
Basic signup and login abuse protection
5. Restore Live Deployment

Pilot-ready ultimately means the complete system is accessible in a live environment.

The final acceptance test is an end-to-end flow:

Signup
  ↓
Login
  ↓
Configure Tenant
  ↓
Add Knowledge
  ↓
Install Widget
  ↓
Customer Conversation
  ↓
Persist Conversation
  ↓
View Tenant Analytics
  ↓
Logout / Login
  ↓
Verify Isolation + Persistence
Phase 4: First Operational Action

After the pilot-ready foundation is complete, the next major product capability is intended to move Magni from:

answers questions

to:

takes useful business action

The first planned workflow is intentionally vendor-neutral:

Lead / Appointment Capture
Customer Conversation
        │
        ▼
Magni Detects Lead Intent
        │
        ▼
Collect Structured Information
        │
        ▼
Create Lead
        │
        ▼
Notify Business

A future internal Lead model can capture information such as:

Customer name
Phone
Email
Requested service
Address
Preferred appointment time
Notes
Status
Source conversation

The initial action can simply create the lead and notify the business by email.

External CRM integrations can then be added behind the same internal service boundary later.

Long-Term Direction

Magni is intended to evolve beyond website chat into an AI customer-operations layer.

Conceptually:

                 Customer Channels

        Chat      Voice      Email      SMS
          \         |          |         /
           \        |          |        /
            └────── MAGNI ─────────────┘
                       │
                Intelligence Layer
                       │
           Understand / Route / Act
                       │
         ┌─────────────┼─────────────┐
         │             │             │
        CRM        Scheduling      Email
         │             │             │
    ServiceTitan     Google        Gmail
    HubSpot          Outlook       Outlook
    Jobber
    Salesforce
    etc.

Magni does not need to replace those systems.

The intended role is to understand customer intent, use existing business systems when appropriate, keep context across interactions, and involve a human when automation should stop.

What I Would Change at 10x Scale

Magni is deliberately optimized for an early-stage, single-instance SaaS deployment.

If usage grew substantially, the architecture would evolve around actual pressure points rather than introducing distributed infrastructure prematurely.

Distributed Rate Limiting

Current application-level safeguards would move toward shared rate-limit infrastructure across multiple application instances.

Distributed Conversation Context

The current per-process live conversation cache could move to shared state such as Redis if horizontal scaling required multiple application instances.

Background Job Processing

Long-running work such as notifications, knowledge ingestion, analytics, synchronization, and external tool actions could move to workers behind a queue.

Semantic Retrieval

Keyword retrieval could be supplemented or replaced by embeddings and semantic search as tenant knowledge bases grow.

PostgreSQL vector capabilities would be a natural first option before introducing a separate vector database.

Tool / Connector Layer

External CRM, scheduling, email, SMS, and voice systems can be abstracted behind Magni-owned capabilities such as:

get_customer()
create_lead()
check_availability()
book_appointment()
send_email()
send_sms()
create_note()
escalate_to_human()

Individual connectors can then implement those capabilities for different vendors without tying Magni's reasoning layer to a specific CRM.

Observability

Larger deployments would justify centralized:

Structured logging
Metrics
Distributed tracing
Model latency tracking
Model usage analytics
Failure monitoring
Tenant cost tracking
Cost-per-conversation metrics
Model Evaluation

A larger evaluation suite would measure:

Routing accuracy
Retrieval quality
Response grounding
Escalation accuracy
Prompt regressions
Latency
Cost per conversation

The goal is to treat model behavior as measurable software behavior rather than something judged only through manual demos.

Technology Stack
Layer	Technology
Backend	Python, Flask
Database	PostgreSQL
ORM	SQLAlchemy
Migrations	Alembic
AI	Google Gemini 2.5
Authentication	Server-side sessions, password hashing
Billing	Stripe
Frontend	HTML, CSS, JavaScript
Deployment	Railway
AI Architecture	Multi-Agent Orchestration
Knowledge Retrieval	Tenant-Scoped Keyword Retrieval
Scheduled Tasks	Railway Cron
Development Approach

Magni was built end to end as a solo project.

AI coding agents were used as development tools during implementation, but architectural decisions, product direction, debugging, integration, security boundaries, and validation remained under my direction.

The project intentionally goes beyond a chatbot demonstration.

It explores the engineering problems that appear when an LLM becomes part of a real SaaS application:

How should multiple agents coordinate?
How should tenant identity propagate through AI workflows?
How should knowledge remain isolated?
How should conversation data remain isolated?
How should concurrent AI usage be limited?
How should human escalation work?
How should schema changes be managed safely?
How should browser sessions be treated as untrusted input?
How should AI costs be bounded?
How should AI behavior and authorization boundaries be tested?
How can the system eventually act through existing business tools without becoming the system of record itself?
Status

Pilot-readiness hardening in progress.

Magni now has a substantially complete SaaS foundation including:

Tenant signup and authentication
Customer portal
PostgreSQL tenant isolation
PostgreSQL conversations and messages
Tenant-scoped knowledge
Multi-agent orchestration
Knowledge-grounded responses
Human escalation
Usage controls
Billing infrastructure
Alembic-managed schema evolution
Automated agent testing
Conversation-ownership regression testing
Embeddable website widget

The immediate objective is to complete the remaining tenant settings, authentication hardening, integration testing, and live deployment work required for the first external pilot.

The next major product milestone after that is lead and appointment capture, allowing Magni to move from answering customer questions to initiating real business workflows.

About

Zakary Marmel
Solo Architect & AI Software Engineer

I build AI-powered software systems that combine LLM capabilities with traditional application engineering.

My professional operations background influences how I approach these systems: the goal is not simply to make an AI model produce impressive output, but to build software that fits into real workflows, has clear authorization and failure boundaries, controls operational cost, and remains understandable when something goes wrong.

GitHub: Z4kM4rm3l
Portfolio: zakarymarmel.netlify.app
