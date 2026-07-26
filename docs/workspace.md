# Permanent User Workspace

OpenScribe exposes a server-rendered user workspace at `/workspace`. Scribe, Account, Preferences, Library, and leader-only Team pages render through one shared shell. System administration remains separate at `/admin`.

Successful full-session login for normal users and team leaders lands directly on this workspace. New navigation, documentation, and successful workspace form redirects use the canonical routes below.

## Canonical routes

- `/workspace`
- `/workspace/account`
- `/workspace/preferences`
- `/workspace/library/templates`
- `/workspace/library/quick-actions`
- `/workspace/library/smart-phrases`
- `/workspace/team/ai-services`
- `/workspace/team/members`
- `/workspace/team/account-requests`

Only the Scribe section loads owner-filtered consultation history. Non-Scribe routes do not query or decrypt transcript history through shell context. Scribe continues to use the owner-authorized workspace resolver for transcript-derived content. Team routes enforce leader access on the server; hiding sidebar links is not an authorization control.

System-admin accounts are redirected to `/admin` rather than receiving the user workspace.

## Consultation return behavior

Browser `sessionStorage` remembers only the active transcript UUID under `openscribe.workspace.lastTranscriptId`. Back-to-Scribe and brand links submit that UUID as an untrusted query hint. Server-side transcript ownership and retention checks remain authoritative. Non-Scribe pages never replace the remembered UUID.

`open_recent=1` opens the existing consultation rail and is then removed from the visible URL with `history.replaceState`.

Create-new-consultation controls in every workspace section use the signed-in user's normalized preferred recording mode. They fall back to whole-file upload only when no supported preference is set.

## Recording navigation lock

The media controller emits recording lifecycle events. Shared workspace code disables only controls marked `data-recording-navigation` while microphone capture is active and installs a `beforeunload` warning. Upload, transcription, generation, and other background states do not emit these events and therefore do not lock navigation.

## Layout and scrolling

Scribe owns a bounded viewport-height shell. The document and outer shell do not scroll; the active note, follow-up, transcript, and consultation-rail regions own their respective overflow. This keeps recording controls and workspace navigation available while reviewing long content.

Non-Scribe workspace sections remain normal document-scrolling pages. Shared form primitives must not apply to Scribe merely because it contains forms: transcriber title and editor fields retain their dedicated transcribe styles, while Account, Preferences, Library, and Team forms retain settings field styling.

Workspace navigation uses one visible text label plus an `aria-label`, so collapsed navigation remains accessible without duplicating labels on pages that do not load transcriber utility CSS. Section icons use the same scale as Create/Recent controls. The desktop collapse control sits beside the current user identity and remains available in expanded and collapsed states.

Settings-derived sections are left-aligned and use the available workspace main width; they do not retain the centered marketing-page gutter from the previous standalone layout. My Library split views remove the generic workspace main padding so their selection rails sit directly beside the permanent sidebar.

At mobile widths, including Scribe, the shared workspace header remains visible and opens the off-canvas navigation drawer. The closed drawer is inert until the user opens it.

## Compatibility redirects

- `/transcribe` issues a temporary redirect to `/workspace` and preserves only `transcript_id`.
- `/settings` issues a temporary redirect through a closed tab map to the corresponding workspace section. It preserves only validated, section-specific editor identifiers; arbitrary query parameters are dropped.
- `GET /home` is a temporary compatibility redirect into the canonical workspace. Allowlisted legacy tab and selected-asset parameters map to their canonical sections; `/home` no longer renders a separate landing page.
- Some established browser mutation handlers retain paths with the `/home` prefix while their forms, feedback, and success redirects live in canonical workspace sections. Those paths are implementation compatibility endpoints, not user navigation or content-sharing boundaries.
- The former `/transcribe-claude`, `/transcribe-glm-2`, and `/transcriber_col_changes` prototype routes have been removed. They are not compatibility endpoints; use `/workspace`.
- Browser POST success redirects and workspace form return metadata should use canonical workspace routes.

Template and quick-action validation-error branches reuse the established server-rendered section context inside the permanent workspace shell, preserving entered values and errors without returning users to `/home`.
