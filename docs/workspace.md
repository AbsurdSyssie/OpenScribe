# Permanent User Workspace

OpenScribe now exposes a server-rendered user workspace at `/workspace`. Scribe,
Account, Preferences, Library, and leader-only Team pages render through one
shared shell. System administration remains separate at `/admin`.

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

The shell loads owner-filtered consultation metadata only. Non-Scribe routes do
not receive transcript text through shell context. Scribe continues to use the
existing owner-authorized workspace resolver for transcript-derived content.
Team routes enforce leader access on the server; hiding sidebar links is not an
authorization control.

## Consultation return behavior

Browser `sessionStorage` remembers only the active transcript UUID under
`openscribe.workspace.lastTranscriptId`. Back-to-Scribe and brand links submit
that UUID as an untrusted query hint. Server-side transcript ownership checks
remain authoritative. Non-Scribe pages never replace the remembered UUID.

`open_recent=1` opens the existing consultation rail, then is removed from the
visible URL with `history.replaceState`.

## Recording navigation lock

The media controller emits recording lifecycle events. Shared workspace code
disables only controls marked `data-recording-navigation` while microphone
capture is active and installs a `beforeunload` warning. Upload, transcription,
generation, and other background states do not emit these events and therefore
do not lock navigation.

## Layout and scrolling

Scribe owns a bounded viewport-height shell. The document and outer shell do
not scroll; the active note, follow-up, transcript, and consultation-rail
regions own their respective overflow. This keeps recording controls and
workspace navigation available while reviewing long content.

Non-Scribe workspace sections remain normal document-scrolling pages. Shared
form primitives must not apply to Scribe merely because it contains forms:
transcriber title and editor fields retain their dedicated Tailwind/transcribe
styles, while Account, Preferences, Library, and Team forms retain settings
field styling.

Workspace navigation uses one visible text label plus an `aria-label`, so
collapsed navigation remains accessible without duplicating labels on pages
that do not load transcriber utility CSS. Section icons use the same 24px scale
as Create/Recent controls. The desktop collapse control sits beside the current
user identity and remains available in both expanded and 64px collapsed states.

Settings-derived sections are left-aligned and use the available workspace main
width; they do not retain the centered marketing-page gutter from the previous
standalone layout.

My Library split views remove the generic workspace main padding so their
selection rails sit directly beside the permanent sidebar.

## Transitional compatibility

Legacy `/transcribe` and `/settings` GET routes issue temporary redirects.
`/transcribe` preserves only `transcript_id`. `/settings` uses a closed tab map
and preserves only validated, section-specific editor identifiers; arbitrary
query parameters are dropped. Browser POST success redirects and workspace form
return metadata use canonical workspace routes.

Template and quick-action validation-error branches still use their legacy
server-rendered error wrapper. They preserve entered values and errors, but
should move into the permanent shell when a shared section-error renderer is
extracted.
