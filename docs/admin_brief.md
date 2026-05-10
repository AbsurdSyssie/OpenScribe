# Admin Brief

## Purpose

This document inventories what `/admin` contains today, without tying future design work to the current arrangement of tabs, panels, forms, or tables.

Goal:
- describe what information exists
- describe what can be changed
- describe who can change it
- separate metadata administration from content access assumptions

This page is for system admins only.

## Layout notes

- `/admin` uses a flat sidebar workspace with card-based sections for providers, defaults, directory, usage, and requests.
- `/admin2` is a system-admin-only dark UI preview for testing `admin2.html`; it reuses the same admin context, POST endpoints, CSRF fields, and return-route handling as `/admin`.
- `/admin2` now exposes the same operational action families as `/admin`: team/user lifecycle, recovery emails/break-glass forms, account-request approval/rejection, provider create/edit/inspect/test/delete, team provider selection clear actions, de-identification assignment/selection controls from the Teams page, default template/quick-action create/edit/duplicate/delete, and usage/failure metadata views.
- `/admin2` splits provider registry into distinct STT, LLM, De-identification, and Clinical NLP pages. Provider rows are created in registry pages; team assignment and selection happen from Workspace -> Teams.
- `/admin2` Teams starts with all teams collapsed; opening a team requires choosing its row, and collapse returns to no open team. People rows show team names and collapse lifecycle/recovery/delete actions into an Actions dropdown.
- `/admin2` Account requests use card-style review rows so request metadata, approval inputs, and rejection inputs do not compete for table-cell space.
- `/admin2` template create/edit forms show EMIS section prompts only while mode is `structured`; switching to `freeform` leaves only the global prompt visible.
- `/admin2` Preferences includes a dark/light theme toggle. Choice is stored in browser `localStorage` as `openscribe_admin2_theme`, avoiding schema changes.
- `/admin2` uses wider content containers for admin-heavy tables/forms: `wide` pages cap at 1440px and `inner` pages cap at 1240px with responsive padding.
- `/admin2` setting-row dividers are scoped to the descriptive cell so horizontal rules do not run beneath adjacent selects/buttons.
- `/admin2` section headings align across two-column grids, use stronger heading weight, and leave more space before/after clear-action button rows.
- `/admin2` custom action dropdowns use a Notion-like popover style. Open menus portal to `body`, layer above other content, scroll up to 70vh/400px, close on click-away/Escape/resize, and auto-close after 3 seconds without hover. Theme remains a simple toggle; native provider `<select>` controls remain browser-native.
- `/admin2` select fields are progressively enhanced into custom listbox dropdowns with the same front-layer, scrollable, click-away, Escape, resize, and hover-timeout behavior. Original `<select>` elements remain present and synced so existing POST handlers continue to receive normal form values.
- `/admin2` Usage has local Overview, Teams, and Providers tabs. Teams shows team-level metadata only; Providers shows provider/model counts, token totals, estimated cost, latency, and success rate without transcript, prompt, or note content.
- `/admin2` Usage > Teams has Active and Suspended status tabs. `/admin2` People table header icons sort by name, age/newest/oldest, team, role, and status. One People filter popover contains team/status selects and uses the same auto-closing dropdown behavior.
- `/admin2` Failures loads aggregate failure source/code/count rows from the same usage metadata context; quick-action forms preserve the `quick-actions` tab on save, duplicate, and delete.
- Provider setup is split into STT, LLM, and de-identification subtabs while preserving the same backend forms and routes.
- LLM and de-identification inspect/ping responses reopen their originating provider subtab instead of resetting to STT.
- Provider and directory cards display operational metadata only; transcript-derived text and generated clinical content remain absent from this page.
- Destructive lifecycle actions remain explicit form submissions with confirmation prompts.

## Audience and access

### Primary user

- system admin

### Not primary users here

- normal users
- team leaders

Notes:
- system admin can manage teams, users, providers, and requests
- system admin still does not gain transcript readability from this page

## Information Inventory

### Admin session context

Contained information:
- signed-in admin identity
- sign-out action
- high-level statement of admin responsibilities
- transient success/error status messaging

System admin can change:
- end session

How:
- sign out

Access:
- system admin only

### Team selection context

Contained information:
- full list of teams
- currently selected team for provider-management scope

System admin can change:
- which team is being targeted for provider administration

How:
- choose team scope

Access:
- system admin only

### STT provider provisioning

Contained information:
- all provisioned STT configs for selected team
- current active team STT selection
- per STT config:
  - label
  - adapter kind
  - base URL
  - transcribe path
  - secret-present flag
  - default model
  - model field name
  - default language
  - language field name
  - response text path and optional segment field mapping
  - extra request-field metadata
  - credential status (`unknown`, `verified`, `partial`, `degraded`, or `invalid`)
  - active flag
- inspection results for candidate endpoint setup
- test results for bundled STT sample

System admin can change:
- create STT config
- inspect candidate STT endpoint before save
- save and inspect a new/updated STT credential in one server-side pass
- re-inspect existing STT configs using the saved Vault reference
- edit STT config metadata
- keep, replace, or remove STT secret references explicitly
- activate/deactivate STT config row
- delete STT config
- test STT config
- set active team STT selection
- clear active team STT selection

How:
- choose target team
- inspect endpoint metadata
- save-and-inspect or edit provisioned config
- confirm duplicate credential warning when intentionally saving same team/adapter/endpoint/key combination
- re-inspect saved config without entering the key again
- run test
- set or clear active team policy

Access:
- system admin only

Notes:
- secret material is stored via Vault reference, not shown back in plaintext
- standalone inspect tokens are not retained after the response; save-and-inspect stores the submitted credential once in Vault and never renders it back
- manual generic STT save-and-inspect tests the saved contract against bundled synthetic audio instead of requiring OpenAPI discovery
- invalid re-inspection clears active STT selections that point at the rejected config
- this area manages metadata and connectivity, not transcript content

### LLM provider provisioning

LLM provider save forms also use explicit `credential_action`. OpenAI and Bedrock providers require a saved bearer token; local optional-token providers such as Ollama default to keeping/no credential when the token field is blank, and can remove a saved token explicitly.

Contained information:
- all provisioned LLM configs for selected team
- current active team LLM selection
- per LLM config:
  - label
  - adapter kind
  - base URL or region-derived endpoint details
  - secret-present state
  - provider model
  - active flag
- inspection results for candidate provider setup
  - model discovery status and warning state

System admin can change:
- create LLM config
- discover models before save
- re-inspect saved LLM provider models using the Vault-backed credential
- edit LLM config metadata
- replace or preserve secret reference
- activate/deactivate LLM config row
- delete LLM config
- set active team LLM selection
- choose allowed model subset for selected team
- choose team default model from allowed subset
- clear active team LLM selection

How:
- choose target team
- discover provider models/metadata
- re-inspect saved provider when models need refreshing without re-entering the key
- save/edit provider config
- set active team LLM policy

Access:
- system admin only

Notes:
- system admin manages provider availability and team policy
- saved-provider model re-inspection reads the Vault reference server-side and does not render the raw key
- this page does not display note/transcript content generated by those providers

### De-identification / PII provider provisioning

Contained information:
- all provisioned de-identification providers
- current selected provider for the selected team
- per provider:
  - label
  - adapter kind
  - base URL
  - selected runtime detect endpoint
  - secret-present state
  - request text/language field names
  - extra JSON body defaults
  - response entity path and entity field names
  - active flag
- OpenAPI/docs inspection results for provider setup
- synthetic ping response for the configured endpoint

System admin can change:
- create or edit a generic REST de-identification provider
- inspect `/docs`, `/redoc`, or `/openapi.json` to discover candidate POST endpoints
- choose the runtime endpoint from discovered candidates
- ping the selected endpoint with synthetic sample text
- tune request body fields and response parsing fields
- save provider metadata and Vault-backed bearer token reference
- assign provider to a team
- select assigned provider for team runtime redaction
- clear team selection back to built-in native Presidio fallback

How:
- choose target team
- enter provider `Base URL`
- enter `OpenAPI/docs path` such as `/docs` or `/openapi.json`
- click `Ping provider` to discover candidate endpoints
- choose a candidate in `Detect path / selected endpoint`
- click `Ping provider` again to test that endpoint's request and response contract
- use raw synthetic ping response to set:
  - `Response entities path`, for example `entities`
  - `Response type field`, for example `label` or `entity_type`
  - `Response score field`, for example `score` or `confidence`
  - optional `Entity type map JSON`, for example `{"NAME":"PERSON"}`
- leave `Request language field` blank unless the API expects a field name such as `lang` or `language`; do not enter the value `en`
- remove extra body fields rejected by the provider as `extra_forbidden`
- save provider
- assign provider to the selected team
- click `Use for team`; runtime redaction uses this selected provider, not the last successful ping

Access:
- system admin only

Notes:
- `OpenAPI/docs path` is for discovery only and is not the runtime endpoint
- `Detect path / selected endpoint` is the POST endpoint saved for runtime redaction
- ping uses synthetic sample text and may display raw provider response for debugging
- runtime redaction sends transcript-derived text only through the selected provider path and does not expose raw provider responses in admin UI
- value-only provider responses are supported when the response includes detected text plus label; OpenScribe matches the detected text back to the submitted source text to derive offsets
- explicit `start` and `end` offsets remain more reliable for repeated identical values or transformed text

### Team records

Contained information:
- all teams
- per team:
  - name
  - status
  - default retention days

System admin can change:
- create team

How:
- enter team name
- choose team status
- choose default retention days
- save team

Access:
- system admin only

Notes:
- current UI emphasizes creation and listing more than in-place editing

### Managed user records

Contained information:
- all users
- per user:
  - name
  - email
  - team
  - onboarding state
  - account status
  - system-admin flag in creation context
  - MFA requirement in creation context

System admin can change:
- create user
- assign user to team
- assign team role
- optionally mark account as system admin
- choose user starting status
- choose MFA requirement
- suspend user
- reactivate suspended/disabled user
- delete user permanently

How:
- enter identity and temporary password
- set role/team/status/admin/MFA fields
- save creation
- use account actions for suspend/reactivate/delete

Access:
- system admin only

Notes:
- deleting a user also deletes owned transcript-derived content
- current user cannot delete self from this page

### Account requests

Contained information:
- all manageable account requests
- per request:
  - requester name
  - requester email
  - requested team name
  - free-text request details
  - request status

System admin can change:
- approve request
- reject request
- choose actual team on approval
- choose team role on approval
- set temporary password on approval
- add review notes

How:
- enter approval details and create user
- enter rejection notes and reject request

Access:
- system admin only

### Usage and observability

Contained information:
- metadata-only usage overview
- no transcript or note content
- optional team filter
- window summaries
- team activity summaries
- per-user activity summaries within selected team
- metrics such as:
  - generation completions
  - generation failures
  - total tokens
  - ingestion job count
  - ingestion failures
  - uploaded megabytes
  - audio hours
  - whole-file upload count
  - live chunk count

System admin can change:
- usage scope filter by team

How:
- choose team filter

Access:
- system admin only

Notes:
- this area is explicitly metadata-only observability
- it should not be redesigned into content browsing

## Capability Summary

### System admin can

- manage team records
- create managed users
- suspend/reactivate/delete users
- provision STT providers
- provision LLM providers
- inspect/test provider configurations
- set or clear active team STT policy
- set or clear active team LLM policy
- define allowed model subsets and defaults
- review and action account requests
- inspect metadata-only usage and activity

### System admin cannot infer from this page

- transcript text
- note text
- redaction source values
- provider secrets in plaintext

## Redesign constraints

Any redesign must preserve:
- system-admin-only access
- metadata administration without transcript readability
- team-scoped provider provisioning
- separate STT and LLM policy concepts
- user lifecycle management
- account-request review
- metadata-only usage visibility
- destructive actions remaining explicit

## Design implications

- `/admin` combines several different admin jobs:
  - provider provisioning
  - team policy
  - directory management
  - user lifecycle
  - request triage
  - observability
- some tasks are high-trust and destructive
- some tasks are exploratory or diagnostic
- provider configuration and provider policy are related but not identical
- usage is observability, not content review

Useful future design split:
- provider setup
- team policy
- directory and lifecycle
- request review
- observability

Current visual direction:
- keep `/admin` as a server-rendered Jinja surface
- use a persistent sidebar for area selection instead of top card tabs
- use flat full-width editing panes with row dividers instead of stacked cards
- keep provider setup, defaults, directory, requests, and usage as separate admin work areas
- keep any future provider-policy split explicit so provisioning and team selection authority remain understandable
- render LLM user-visible models as multi-column selectable tiles, with team default model chosen from a dropdown populated only by enabled visible models

## Recommended use of this brief

Use this document as an admin capability map:
- not “how current admin layout is organized”
- but “what `/admin` must expose and protect”

That should make it easier to redesign admin UX while preserving the privacy and authority boundaries that matter.
