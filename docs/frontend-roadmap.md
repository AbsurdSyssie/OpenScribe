# Frontend Roadmap

## Summary

OpenScribe will stay server-rendered for now, using FastAPI + Jinja templates for login, admin, and the early user surfaces.

The long-term frontend direction is **Next.js App Router**. We are not introducing it yet. The immediate goal is to harden the current browser auth/security model and keep the backend contract stable so the frontend can later move without redoing core business logic.

## Current Direction

### Active frontend today

- FastAPI + Jinja templates
- server-rendered forms and page navigation
- cookie-based auth

### Why React is not required yet

- the current UI surface is still small
- the app is shipping backend-heavy platform work, not complex client-side interaction
- adding React now would increase auth, routing, and deployment complexity before the product needs it

## Phase 1 — Harden the Current Server-Rendered Frontend

Keep the current FastAPI + Jinja frontend and improve the browser security model.

Decisions:

- move away from readable signed client-side session payloads
- adopt **server-side sessions backed by Redis**
- add **CSRF protection** for browser form posts and future AJAX-style requests
- keep `SameSite=Lax` for localhost testing
- defer strict `Secure` cookie enforcement until non-localhost deployment

Chosen libraries:

- sessions: `starsessions`
- CSRF: `fastapi-csrf-protect`

This phase should keep the current UI architecture intact while removing unnecessary custom auth/session boilerplate.

## Phase 2 — Stabilize the Backend Contract

Prepare the backend so a future frontend rewrite is low-risk.

Rules:

- keep `/api/v1` as the canonical API boundary
- keep authorization and ownership enforcement in FastAPI services/dependencies
- do not move business logic into templates
- keep auth decisions backend-owned, not frontend-owned

This means the frontend can change later without changing the trust boundary.

## Phase 3 — Introduce Next.js

When the product needs a richer frontend, adopt **Next.js App Router** as a separate frontend app inside the repo.

Recommended structure later:

- backend remains FastAPI
- frontend is added as a separate `frontend/` app
- frontend consumes the backend through the existing API boundary

Why Next.js:

- it is the intended long-term React framework choice for this project
- it supports a gradual move from basic pages to more interactive product surfaces
- it provides a stronger foundation than plain React for a full application frontend

## Phase 4 — Migrate Surfaces Gradually

Do not rewrite everything at once.

Recommended migration order:

1. login and authenticated landing pages
2. admin/team/user management surfaces
3. transcript and generated-document workflows
4. richer interactive note-generation surfaces

During this phase:

- keep FastAPI templates as fallback until the equivalent Next.js routes are complete
- avoid a big-bang cutover
- keep backend auth and ownership behavior unchanged

## Non-Negotiable Frontend Rules

- do not store transcript-derived content in `localStorage` or `sessionStorage`
- do not expose session tokens to browser JavaScript unless architecture is explicitly changed
- do not move authorization decisions into client code
- do not let the future Next.js app become the source of truth for permissions or object ownership

## Current Chosen Defaults

- current frontend: FastAPI + Jinja
- long-term frontend: Next.js App Router
- current hardening direction: Redis-backed server-side sessions plus CSRF protection
- current backend authority: FastAPI remains the auth, authorization, and data ownership source of truth
