# Frontend Roadmap

## Status

This is a roadmap, not the current frontend or authentication contract. Current implemented browser behavior is documented in [workspace.md](workspace.md), [auth.md](auth.md), and [security.md](security.md).

OpenScribe remains server-rendered with FastAPI, Jinja templates, same-origin JavaScript, and CSS/assets served by the application. A future Next.js App Router frontend remains an option, but no `frontend/` Next.js application exists in the repository today.

## Implemented frontend and browser security

Current behavior includes:

- FastAPI + Jinja page rendering;
- server-rendered forms and same-origin API requests;
- an opaque session token in an `HttpOnly` cookie;
- hashed session tokens and explicit session state in PostgreSQL;
- a separate opaque trusted-device token with server-side state;
- project-owned HMAC CSRF tokens bound to an anonymous nonce before login or the active session after login;
- same-origin `Origin`/`Referer` enforcement for unsafe cookie-authorized API requests;
- nonce-based Content Security Policy and locally served browser dependencies;
- the permanent `/workspace` shell for Scribe, Account, Preferences, Library, and leader Team sections;
- transitional compatibility routes while older `/home` rendering is retired.

The earlier proposal to adopt `starsessions`, Redis-backed sessions, and `fastapi-csrf-protect` was not implemented. Redis is used for Celery and rate limiting; authentication sessions are database-backed and CSRF is implemented in the application.

## Current direction

The immediate frontend work should continue to:

- consolidate browser surfaces into the permanent workspace and the separate admin workspace;
- keep authentication, authorization, ownership, retention, encryption, and provider policy in backend services and dependencies;
- keep browser assets same-origin and compatible with the enforced CSP;
- preserve accessible server-rendered fallbacks for state-changing forms;
- avoid introducing a second source of truth for routes or permissions.

## Future Next.js decision

Adopt Next.js only when the product requires a richer client application and the additional auth, deployment, caching, and routing complexity is justified. Before implementation, write a focused architecture decision covering:

- deployment topology and origin boundaries;
- cookie and CSRF behavior across the frontend/backend boundary;
- server-side rendering and cache controls for authenticated or transcript-derived data;
- API error and session-expiry handling;
- CSP and local asset requirements;
- a route-by-route migration and rollback plan.

Recommended future repository shape:

- FastAPI remains the backend authority;
- a separate `frontend/` application consumes `/api/v1`;
- the backend remains responsible for permissions and object ownership;
- existing Jinja pages remain available until each replacement route is complete and verified.

## Suggested migration order

1. low-risk authenticated navigation and preference surfaces;
2. team and administrative metadata surfaces;
3. transcript workspace shell and history navigation;
4. recording, streaming, generated-document, and other high-interaction workflows.

This order is deliberately different from the earlier login-first proposal: authentication pages are small, security-sensitive, and already work without a client framework. Moving them first would add cross-stack session and CSRF complexity before delivering substantial product value.

## Non-negotiable rules

- Do not store transcript-derived content in `localStorage` or `sessionStorage`.
- A non-sensitive active transcript UUID may be retained as an untrusted navigation hint only where [workspace.md](workspace.md) documents it.
- Do not expose session or trusted-device tokens to browser JavaScript.
- Do not move authorization or ownership decisions into client code.
- Do not cache authenticated, transcript, generated-document, or account-flow responses publicly.
- Do not load production runtime JavaScript, CSS, fonts, WASM, models, or other executable dependencies from public CDNs.
- Keep `/api/v1` as the canonical programmatic boundary and update [api.md](api.md) when it changes.
