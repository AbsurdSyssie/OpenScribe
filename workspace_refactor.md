# OpenScribe Permanent Workspace Implementation Plan

## 1. Objective

Replace the separate transcriber and settings layouts with one permanent workspace shell for regular users and team leaders.

The permanent shell must:

* Keep the existing collapsible, resizable transcriber sidebar.
* Keep **Create new consultation** at the top on every workspace section.
* Show **Recent consultations** while the Scribe workspace is active.
* Show **Back to Scribe** instead of Recent consultations while another section is active.
* Render Account, Preferences, My Library, and Team sections in the main content area.
* Keep the sidebar present while navigating between sections.
* Return users to their previous consultation when they return to Scribe.
* Open the recent-consultation rail when returning through **Back to Scribe**.
* Prevent navigation away from Scribe while microphone recording is active.
* Allow navigation while uploads, transcription, note generation, and other background jobs are running.
* Keep system administration separate.
* Reuse the current settings forms, services, partials, validation, permissions, and POST handlers.
* Preserve old `/settings` and `/transcribe` links during migration.

This is a server-rendered workspace. Do not turn the application into a client-side SPA.

---

# 2. Locked Product Decisions

Treat the following as requirements, not suggestions.

## 2.1 Permanent sidebar

The workspace sidebar stays visible across:

* Scribe
* Account
* Preferences
* My Templates
* My quick actions
* Smart phrases
* AI services
* Team members
* Account requests
* Template and quick-action editor subviews

On mobile, it becomes a drawer rather than remaining physically visible.

## 2.2 Sidebar order

Use this order:

### Primary action

1. Create new consultation

### Scribe navigation

When the current section is Scribe:

2. Recent consultations

When the current section is not Scribe:

2. Back to Scribe

### Personal

3. Account
4. Preferences

### My Library

5. My Templates
6. My quick actions
7. Smart phrases

### Team — leaders only

8. AI services
9. Team members
10. Account requests

### Footer

11. Sign out

Do not retain a generic **Settings** navigation item.

## 2.3 Create new consultation

**Create new consultation must remain visible at the top in every section.**

Clicking it must:

1. Create the consultation through the existing creation handler.
2. Redirect into the Scribe workspace.
3. Open the newly created consultation.
4. Replace the current non-Scribe content with the Scribe workspace.

It must be disabled during active microphone recording.

## 2.4 Back to Scribe

When viewing any non-Scribe section, replace the Recent consultations control with **Back to Scribe**.

Clicking it must:

1. Return to the last consultation the user had open.
2. Fall back to the most recent consultation if the previous one no longer exists.
3. Fall back to the empty Scribe state if the user has no consultations.
4. Open the recent-consultation rail automatically.

## 2.5 Consultation selection

Clicking a consultation tile must always:

1. Navigate to the Scribe workspace.
2. Open the selected consultation.
3. Mark that consultation as the active one.
4. Preserve normal browser history.

## 2.6 Brand behaviour

The OpenScribe brand/logo must no longer navigate to `/home`.

It must use the same destination logic as **Back to Scribe**:

* Restore the last-opened consultation.
* Otherwise open the most recent consultation.
* Otherwise show the empty Scribe state.

When returning from a non-Scribe section, it should also open the recent-consultation rail.

## 2.7 Recording lock

While microphone recording is active:

* Disable Create new consultation.
* Disable consultation switching.
* Disable Account, Preferences, Library, and Team navigation.
* Disable the brand’s Back-to-Scribe navigation if it would trigger a page navigation.
* Leave sidebar collapse, expansion, resize, and the recording controls usable.
* Add a browser unload warning for refresh, close, direct URL entry, and browser Back.

Use this message for disabled navigation:

> Finish or cancel the recording before leaving Scribe.

Do not use generic “busy” state to determine whether navigation is disabled. Only active microphone recording should trigger this lock.

## 2.8 Background work

Do not block navigation merely because any of these are active:

* Uploaded audio processing
* Server-side transcription
* Clinical-note generation
* Follow-up generation
* Quick-action generation
* Autosave requests
* Other asynchronous server jobs

When the user returns to Scribe, reload the latest job and document state from the server.

## 2.9 Admin boundary

This project covers:

* Regular users
* Team leaders/managers

It does not merge the system-admin interface into the workspace.

Do not modify the admin shell except where shared backend helpers require a safe refactor.

---

# 3. Canonical Routes

Use `/workspace` as the canonical route group.

## 3.1 Route map

| Section               | Canonical URL                      |
| --------------------- | ---------------------------------- |
| Scribe                | `/workspace`                       |
| Specific consultation | `/workspace?transcript_id=<id>`    |
| Account               | `/workspace/account`               |
| Preferences           | `/workspace/preferences`           |
| My Templates          | `/workspace/library/templates`     |
| My quick actions      | `/workspace/library/quick-actions` |
| Smart phrases         | `/workspace/library/smart-phrases` |
| AI services           | `/workspace/team/ai-services`      |
| Team members          | `/workspace/team/members`          |
| Account requests      | `/workspace/team/account-requests` |

Nested editor routes should also use the workspace shell. Suggested routes:

| Subview           | Suggested URL                                                                                                         |
| ----------------- | --------------------------------------------------------------------------------------------------------------------- |
| New template      | `/workspace/library/templates/new`                                                                                    |
| Edit template     | `/workspace/library/templates/<template_id>/edit`                                                                     |
| New quick action  | `/workspace/library/quick-actions/new`                                                                                |
| Edit quick action | `/workspace/library/quick-actions/<action_id>/edit`                                                                   |
| Edit smart phrase | Use the existing interaction if it is modal/inline; otherwise use `/workspace/library/smart-phrases/<phrase_id>/edit` |

Do not introduce client-side pseudo-routes.

## 3.2 Compatibility redirects

Retain compatibility routes during migration.

### `/transcribe`

Redirect:

```text
/transcribe
→ /workspace
```

Preserve supported query parameters:

```text
/transcribe?transcript_id=abc
→ /workspace?transcript_id=abc
```

Only preserve query parameters the existing transcriber explicitly supports. Do not blindly forward arbitrary parameters.

### `/settings`

Map the existing tabs:

```text
/settings?tab=account
→ /workspace/account

/settings?tab=preferences
→ /workspace/preferences

/settings?tab=templates
→ /workspace/library/templates

/settings?tab=quick-actions
→ /workspace/library/quick-actions

/settings?tab=smart-phrases
→ /workspace/library/smart-phrases

/settings?tab=ai-services
→ /workspace/team/ai-services

/settings?tab=team-members
→ /workspace/team/members

/settings?tab=team-management
→ /workspace/team/members

/settings?tab=account-requests
→ /workspace/team/account-requests
```

Unknown or missing tabs should redirect to:

```text
/workspace/preferences
```

Use temporary redirects while development is active. Change to permanent redirects only after the old routes have been stable for at least one release and no internal forms still target them.

## 3.3 `/home`

Do not remove `/home` in the first implementation.

For this task:

* Remove `/home` from the workspace logo.
* Remove workspace navigation that depends on `/home`.
* Leave the route itself available for compatibility.

A later cleanup can redirect regular users from `/home` to `/workspace`.

---

# 4. Architecture

## 4.1 Do not render the full transcriber context on every page

The current Scribe workspace likely loads significantly more data than Account or Preferences need.

Split the page context into two levels.

### Shared shell context

Create a helper with a name similar to:

```python
build_workspace_shell_context(...)
```

It should provide only data required by the permanent shell:

* Current user
* User name and email
* User role
* Whether the user is a team leader/manager
* Whether Personal, Library, and Team groups should display
* Recent consultations
* Last or active transcript ID
* Active workspace section
* Canonical Back-to-Scribe URL
* Canonical New Consultation action
* Flash message data
* CSRF data
* Sidebar feature flags
* Mobile/sidebar state defaults

### Scribe context

Keep the existing transcriber context builder for:

* Active transcript
* Transcript versions
* Generated note
* Follow-ups
* History
* Dictation
* Recording mode
* STT state
* LLM state
* Upload state
* Generation state
* Document polling
* All other transcriber-only data

The Scribe route should:

1. Build the shared shell context.
2. Build the existing transcriber context.
3. Merge the two contexts.
4. Render the shared workspace shell with Scribe as its main content.

### Section-specific settings context

Each non-Scribe route should:

1. Authorise the current user.
2. Build the shared shell context.
3. Load only data needed by that section.
4. Render the shared shell with the appropriate content partial.

Do not query transcript documents, generated notes, transcript history, or provider runtime state unless the selected page needs them.

## 4.2 Define a workspace-section identifier

Avoid scattering unrelated strings through route handlers and templates.

Define constants or an enum-like structure such as:

```python
WORKSPACE_SCRIBE = "scribe"
WORKSPACE_ACCOUNT = "account"
WORKSPACE_PREFERENCES = "preferences"
WORKSPACE_TEMPLATES = "templates"
WORKSPACE_QUICK_ACTIONS = "quick-actions"
WORKSPACE_SMART_PHRASES = "smart-phrases"
WORKSPACE_AI_SERVICES = "ai-services"
WORKSPACE_TEAM_MEMBERS = "team-members"
WORKSPACE_ACCOUNT_REQUESTS = "account-requests"
```

Every workspace response should supply:

```python
active_workspace_section
```

Use this for:

* Active sidebar link
* Page title
* Main partial selection
* Conditional asset loading
* Back-to-Scribe versus Recent consultations
* Mobile heading
* Parent navigation state for editor subviews

Editor pages should mark their parent section active.

Examples:

* Template editor → `templates`
* Quick-action editor → `quick-actions`
* Team-member detail → `team-members`

---

# 5. Template Structure

## 5.1 Create one shared outer template

Create a template such as:

```text
app/templates/workspace.html
```

Suggested high-level structure:

```jinja2
<!DOCTYPE html>
<html>
  <head>
    shared workspace assets
    conditional section assets
  </head>
  <body data-workspace-section="{{ active_workspace_section }}">
    <div class="workspace-shell">
      {% include "workspace/_sidebar.html" %}
      {% include "workspace/_mobile_header.html" %}
      <main class="workspace-main">
        flash messages
        selected main content
      </main>
      optional recent-consultation rail
    </div>

    shared workspace scripts
    conditional section scripts
    CSRF script
  </body>
</html>
```

Use the project’s existing base-template conventions if there is already a safe shared base. Do not create a second competing global layout system.

## 5.2 Reuse the transcriber sidebar

Start with:

```text
app/templates/transcribe/_sidebar.html
```

Either:

* Move it to `app/templates/workspace/_sidebar.html`, or
* Keep the file in place but make it section-agnostic.

Moving it is cleaner, but only do so after finding every include and test that references the existing path.

The sidebar must not require the full Scribe context.

## 5.3 Main-content dispatch

Prefer explicit includes over dynamically constructing arbitrary template paths.

Example:

```jinja2
{% if active_workspace_section == "scribe" %}
  {% include "transcribe/_workspace.html" %}
{% elif active_workspace_section == "account" %}
  {% include "settings/_account.html" %}
{% elif active_workspace_section == "preferences" %}
  {% include "settings/_preferences.html" %}
...
{% endif %}
```

A route may alternatively provide a trusted internal `workspace_content_template` value. Do not allow request data to directly choose a template path.

## 5.4 Settings partials

Reuse these existing partials:

```text
app/templates/settings/_account.html
app/templates/settings/_preferences.html
app/templates/settings/_template_library.html
app/templates/settings/_quick_action_library.html
app/templates/settings/_quick_action_editor.html
app/templates/settings/_smart_phrase_library.html
app/templates/settings/_ai_services.html
app/templates/settings/_team_members.html
app/templates/settings/_account_requests.html
```

Inspect every partial before editing.

Many may currently assume:

* `settings_tab`
* `.settings-page`
* `.settings-main`
* The standalone settings sidebar
* All settings content existing in the DOM at once

Remove only those assumptions.

Do not alter:

* Input names
* Form actions without updating their handlers
* CSRF fields
* Permission checks
* Validation messages
* Existing data attributes used by JavaScript
* Existing service calls

## 5.5 Retire the old settings shell gradually

Do not delete `app/templates/settings.html` immediately.

Migration sequence:

1. Build and test the new workspace routes.
2. Change `/settings` to compatibility redirects.
3. Confirm no handler still renders `settings.html`.
4. Search the repository for `settings.html`.
5. Search for links beginning with `/settings`.
6. Search for redirects to `/settings`.
7. Remove the old template only after all references are gone.
8. Keep the settings content partials.

---

# 6. Sidebar Implementation

## 6.1 Top area

Keep:

* Brand
* Current user identity
* Collapse control
* Resize control
* Create new consultation

Change the brand destination from `/home` to the canonical Back-to-Scribe destination.

## 6.2 Create new consultation form

The sidebar button currently references a separate form. Ensure that form exists on every workspace section.

Place the hidden creation form in the shared shell rather than only in the Scribe template.

Example structure:

```jinja2
<form
  id="new-session-form"
  method="post"
  action="/workspace/consultations/new"
  hidden>
  CSRF field
</form>
```

Reusing the current POST path is acceptable initially. The important requirement is that its redirect becomes canonical:

```text
/workspace?transcript_id=<new-id>
```

## 6.3 Scribe state

When:

```python
active_workspace_section == "scribe"
```

render:

* Recent consultations toggle
* Existing session rail/panel controls
* Active consultation styling
* Delete-selection controls where currently supported

## 6.4 Non-Scribe state

When another section is active, render:

* Back arrow icon
* “Back to Scribe”
* A normal anchor with a functional server fallback
* A data attribute allowing JavaScript to append the remembered transcript ID

Suggested markup behaviour:

```html
<a
  href="/workspace?open_recent=1"
  data-back-to-scribe>
  Back to Scribe
</a>
```

## 6.5 Navigation groups

Use semantic navigation:

```html
<nav aria-label="Workspace navigation">
```

Each group should have a visible group label.

Apply:

```html
aria-current="page"
```

to the selected item.

For collapsed sidebar mode:

* Show icons.
* Preserve accessible labels.
* Provide titles/tooltips.
* Do not render unlabeled icon-only controls.

Suggested icons:

| Item             | Icon                 |
| ---------------- | -------------------- |
| Account          | `user-round`         |
| Preferences      | `sliders-horizontal` |
| My Templates     | `files`              |
| My quick actions | `zap`                |
| Smart phrases    | `text-cursor-input`  |
| AI services      | `bot`                |
| Team members     | `users`              |
| Account requests | `user-round-plus`    |
| Sign out         | `log-out`            |
| Back to Scribe   | `arrow-left`         |

Use the icons already present in the settings navigation where possible.

## 6.6 Role conditions

Preserve the existing visibility rules.

### Personal

Visible to regular authenticated workspace users.

### My Library

Visible when:

* The user is not a system-only administrator.
* The user belongs to a team.
* Existing application rules allow those assets.

### Team

Visible only when the current user satisfies the existing manager/team-leader condition.

Do not rely only on hiding the link. Every route must enforce the corresponding permission server-side.

## 6.7 Footer

Move the current settings sign-out form into the permanent workspace sidebar footer.

Do not include a Return to Scribe link in the footer.

---

# 7. Remembering the Last Consultation

Use browser session storage for the initial implementation. This avoids adding database fields or trusting an unvalidated persistent cookie.

## 7.1 Storage key

Use one clearly namespaced key:

```text
openscribe.workspace.lastTranscriptId
```

## 7.2 Writing the value

Whenever Scribe loads with a valid active transcript:

```javascript
sessionStorage.setItem(
  "openscribe.workspace.lastTranscriptId",
  activeTranscriptId
);
```

Place the active transcript ID in trusted server-rendered markup:

```html
<body data-active-transcript-id="{{ active_transcript_id or '' }}">
```

Do not derive it by parsing visible text.

## 7.3 Back-to-Scribe navigation

Shared workspace JavaScript should:

1. Listen for a click on `[data-back-to-scribe]`.
2. Read the stored transcript ID.
3. If one exists, navigate to:

```text
/workspace?transcript_id=<encoded-id>&open_recent=1
```

4. If none exists, use:

```text
/workspace?open_recent=1
```

The server must still validate transcript ownership.

## 7.4 Invalid stored IDs

If the stored transcript:

* Was deleted
* Belongs to another user
* Is malformed
* Is no longer accessible

the server must not reveal whether it exists.

For the remembered-ID path:

1. Ignore the invalid remembered value.
2. Resolve the user’s most recent accessible consultation.
3. Otherwise render the empty Scribe state.

For an explicitly entered URL containing an unauthorised `transcript_id`, preserve the current security behaviour rather than silently exposing or switching ownership context.

## 7.5 Opening the recent rail

When `open_recent=1` is present and Scribe has loaded:

1. Open the existing recent-consultation rail.
2. Set the toggle’s `aria-expanded` correctly.
3. Focus the rail heading or first appropriate control on mobile.
4. Remove `open_recent=1` from the visible URL with `history.replaceState`.
5. Preserve `transcript_id` in the cleaned URL.

This prevents every refresh from repeatedly reopening the rail.

---

# 8. Recording Navigation Lock

## 8.1 Source of truth

Use the actual browser recording state.

Do not infer the lock from:

* Transcript database status alone
* A generic loading spinner
* Upload state
* Generation state
* `data-local-busy-protected`
* A disabled transcription button

Expose a single function from the recording/media module, for example:

```javascript
isRecordingActive()
```

or dispatch events:

```text
openscribe:recording-started
openscribe:recording-stopped
```

Events are preferable because the shared shell can react without tightly coupling itself to the recording implementation.

## 8.2 Lockable elements

Mark relevant elements with:

```html
data-recording-navigation
```

Include:

* Create new consultation
* Consultation tiles
* Account
* Preferences
* Templates
* Quick actions
* Smart phrases
* AI services
* Team members
* Account requests
* Brand navigation
* Any other link that leaves the active consultation

Do not include:

* Sidebar collapse
* Sidebar resize
* Recent rail open/close
* Recording controls
* Current-consultation internal tabs

## 8.3 Applying the disabled state

For buttons:

```javascript
button.disabled = true;
```

For anchors:

* Set `aria-disabled="true"`.
* Set `tabindex="-1"` while locked.
* Add a disabled CSS class.
* Prevent click navigation.
* Preserve and restore the previous tabindex when unlocked.

Add the tooltip/title:

```text
Finish or cancel the recording before leaving Scribe.
```

Do not replace the original href. This ensures normal navigation is restored cleanly.

## 8.4 Browser unload warning

While recording is active, register `beforeunload`.

Remove the listener as soon as recording stops or is cancelled.

Modern browsers display their own generic warning. Do not depend on custom warning text.

Do not attempt to trap users permanently with repeated `pushState` manipulation.

## 8.5 Initial state

On page load:

* Recording navigation must be enabled.
* Starting recording applies the lock.
* Stopping, cancelling, or successfully finalising recording removes it.
* A recording error must also remove it once recording is no longer active.

## 8.6 Tests

Test at least:

* Start recording disables all leaving-navigation controls.
* Sidebar collapse remains enabled.
* Recent rail remains usable.
* Stop recording restores navigation.
* Cancel recording restores navigation.
* Recording failure restores navigation.
* Uploading without recording does not disable navigation.
* Note generation does not disable navigation.

---

# 9. CSS Refactor

## 9.1 Shared shell CSS

Extract permanent-shell styles from `transcribe.css` into a file such as:

```text
app/static/css/workspace.css
```

Move only styles for:

* Outer shell
* Sidebar
* Sidebar collapse
* Sidebar resize
* Sidebar navigation
* Sidebar footer
* Mobile drawer
* Workspace main area
* Recent-consultation rail
* Recording-disabled navigation
* Shared flash messages

Keep transcription-content-specific styles in:

```text
app/static/css/transcribe.css
```

## 9.2 Settings CSS

Existing settings styles may depend on:

```css
.settings-page
.settings-shell
.settings-sidebar
.settings-main
```

The old settings sidebar will no longer exist.

Refactor selectors so settings content is scoped beneath something such as:

```css
.workspace-section
.workspace-section--settings
.settings-content
```

Do not use broad selectors such as:

```css
main button
section input
form label
```

These may unintentionally restyle Scribe.

## 9.3 Active and disabled states

Provide explicit classes for:

```css
.workspace-nav__link[aria-current="page"]
.workspace-nav__link[aria-disabled="true"]
.workspace-sidebar--collapsed
.workspace-sidebar--mobile-open
```

Disabled navigation must look disabled but remain readable.

Do not use colour alone to communicate the disabled state.

## 9.4 Responsive behaviour

Desktop:

* Sidebar retains collapse and resize behaviour.
* Main content fills remaining width.
* Recent rail behaves as it currently does.

Mobile:

* Sidebar starts closed.
* A menu button appears in the workspace header.
* Sidebar opens as an overlay/drawer.
* Escape closes it.
* Clicking a navigation item closes it.
* Focus returns to the menu button after closing.
* Body scrolling is controlled while the drawer is open.
* Recent consultation rail must not appear behind the sidebar drawer.

---

# 10. JavaScript Refactor

## 10.1 Shared workspace module

Create a shared module such as:

```text
app/static/js/workspace/app.js
```

It should initialise:

* Sidebar collapse
* Sidebar resize
* Mobile drawer
* Back-to-Scribe behaviour
* Last-transcript memory
* Active navigation
* Recording navigation lock
* Recent rail auto-open
* Shared icon refresh if needed

Do not move transcription-specific recording or document-generation logic into this module.

## 10.2 Session rail

The branch already has a dedicated session-rail module.

Adapt it so:

* It runs only when the relevant rail markup exists.
* It tolerates non-Scribe sections.
* It can be opened from `open_recent=1`.
* Consultation links use `/workspace?transcript_id=...`.
* Selecting a consultation always enters Scribe.

Avoid duplicate rail implementations.

## 10.3 Settings JavaScript

The current settings template includes inline logic for:

* Mobile settings menu
* Confirmation prompts
* Service configuration toggles
* STT selection
* LLM selection
* Temporary-password copying
* Dirty-form warnings

Move reusable logic into:

```text
app/static/js/settings/app.js
```

Remove the old settings-specific mobile menu because the permanent workspace drawer replaces it.

Retain:

* Confirmation handling
* STT/LLM form behaviour
* Password copy behaviour
* Dirty-form protection
* Smart-phrase module
* Template editor module

Every initializer must safely do nothing when its target element is absent.

## 10.4 Conditional asset loading

Always load:

* Shared tokens/components
* Workspace shell CSS
* Workspace shell JavaScript
* Lucide
* CSRF support

Load Scribe assets only on Scribe.

Load settings assets only on relevant settings/library/team pages.

Load smart-phrase JavaScript only for Smart phrases.

Load template-editor JavaScript only for template editor pages.

This reduces accidental cross-page behaviour and keeps the shell performant.

---

# 11. Backend Route Refactor

## 11.1 Add canonical workspace GET handlers

Add one handler per route or use a small internal dispatcher with explicit route functions.

Do not build one giant handler containing every page’s business logic.

Suggested shape:

```python
@router.get("/workspace")
def workspace_scribe(...):
    ...

@router.get("/workspace/account")
def workspace_account(...):
    ...

@router.get("/workspace/preferences")
def workspace_preferences(...):
    ...
```

Each handler should remain easy to test independently.

## 11.2 Shared render helper

Create a helper similar to:

```python
def render_workspace(
    request,
    *,
    current_user,
    active_section,
    section_context=None,
    status_code=200,
):
    ...
```

Responsibilities:

* Build shell context.
* Merge section context.
* Select trusted content partial.
* Return the shared template response.

Do not place authorisation decisions only inside this renderer. Routes must authorise before rendering.

## 11.3 Reuse existing settings data loaders

Find the existing `/settings` handler and extract data-loading branches into functions.

Examples:

```python
build_account_context(...)
build_preferences_context(...)
build_template_library_context(...)
build_quick_action_library_context(...)
build_smart_phrase_context(...)
build_ai_services_context(...)
build_team_members_context(...)
build_account_requests_context(...)
```

Do not copy and paste database queries into the new routes.

## 11.4 POST redirects

Search all settings and library POST handlers.

Replace redirects such as:

```text
/settings?tab=preferences
/settings?tab=templates
/settings?tab=team-members
```

with canonical workspace destinations.

Use HTTP 303 after successful form POSTs.

Preserve:

* Flash messages
* Validation-error behaviour
* Temporary-password handling
* Editor return destinations
* Selected item IDs when required

## 11.5 Validation errors

If a form currently re-renders the settings page with errors rather than redirecting:

* Render the same workspace route.
* Keep the correct sidebar section active.
* Keep entered values.
* Keep field-level errors.
* Do not fall back to Preferences.

## 11.6 System-admin handling

For system-admin-only accounts:

* Preserve existing access rules.
* Do not expose leader workspace routes merely because the sidebar link is hidden.
* Redirect to `/admin` where that matches current behaviour, or use the current forbidden response convention.

Do not invent a new authorisation model during this work.

---

# 12. File-by-File Worklist

The developer should verify the exact current contents before editing.

## Core routes and context

### `app/routes/web_home_transcribe.py`

Likely work:

* Extract settings-section context builders.
* Add canonical workspace section handlers if this file remains the correct route owner.
* Replace old settings rendering.
* Update form redirects.
* Add compatibility redirect mapping.
* Avoid turning the file into an unmaintainable monolith; split into a workspace route module if necessary.

### `app/routes/web_transcribe.py`

Likely work:

* Make `/workspace` the canonical GET entry.
* Preserve the existing transcriber functionality.
* Convert `/transcribe` into a compatibility route or alias.
* Update route-base values used by consultation links.

### `app/web/transcribe_workspace.py`

Likely work:

* Split shared sidebar/session-list context from full Scribe content.
* Ensure recent consultations can be loaded for non-Scribe pages without loading the full transcript workspace.
* Keep existing ownership filtering.

### `app/web/presentation.py`

Likely work:

* Update canonical route fields.
* Add workspace navigation presentation values if this module owns them.
* Keep labels and timestamps centralised.

## Templates

### `app/templates/transcribe/_sidebar.html`

* Convert to permanent workspace navigation.
* Keep Create new consultation always visible.
* Add conditional Recent consultations/Back to Scribe.
* Add Personal, My Library, Team groups.
* Move Sign out into footer.
* Replace `/home` brand link.
* Remove generic Settings link.

### `app/templates/transcribe/_session_panel.html`

* Update consultation URLs.
* Ensure it supports automatic opening.
* Ensure it is safe when no consultations exist.

### `app/templates/transcribe/_workspace.html`

* Keep Scribe content only.
* Remove assumptions that it owns the global page shell.
* Do not place shared sidebar forms only in this partial.

### `app/templates/transcribe/_shell_extras.html`

* Inspect hidden forms/modals currently required globally.
* Move the new-session form into the shared workspace shell.
* Leave Scribe-specific dialogs in the Scribe section.

### `app/templates/settings.html`

* Stop using it as the active shell.
* Keep temporarily until redirects and tests are complete.
* Delete only in the final cleanup phase.

### Settings partials

* Remove dependency on the old settings sidebar.
* Render correctly as individual workspace sections.
* Preserve forms and data attributes.

### Template editor files

Inspect:

```text
app/templates/template_editor.html
app/templates/_template_editor_workspace.html
app/templates/_template_editor_script.html
```

Ensure template editing stays inside the permanent shell and keeps My Templates active.

## CSS

### `app/static/css/transcribe.css`

* Extract permanent shell styles.
* Keep Scribe styles.
* Verify no regressions to resize/collapse/rail behaviour.

### `app/static/css/settings.css`

* Remove standalone-page assumptions.
* Namespace settings content under workspace classes.

### `app/static/css/settings-smart-phrases.css`

* Verify selectors do not depend on `.settings-page`.
* Keep the editor usable inside the workspace main panel.

### New shared file

Suggested:

```text
app/static/css/workspace.css
```

## JavaScript

### `app/static/js/transcribe/app.js`

* Stop owning shell-wide navigation where practical.
* Emit recording start/stop events.
* Preserve existing Scribe bootstrapping.

### `app/static/js/transcribe/media.js`

* Expose or dispatch authoritative recording state changes.
* Ensure errors and cancellation unlock navigation.

### `app/static/js/transcribe/sessionRail.js`

* Update canonical URLs.
* Support `open_recent=1`.
* Remain safe when markup is absent.

### `app/static/js/transcribe/layout.js`

* Move reusable sidebar collapse/resize behaviour into the workspace module where practical.

### `app/static/js/transcribe/mobile.js`

* Consolidate mobile sidebar behaviour into the shared workspace drawer.

### `app/static/js/settings/smart-phrases.js`

* Ensure it initialises only on its section.
* Preserve all current editor behaviours.

### New shared module

Suggested:

```text
app/static/js/workspace/app.js
```

---

# 13. Implementation Sequence

Use small, reviewable commits.

## Phase 0 — Baseline

1. Check out `cams_changes`.
2. Read `AGENTS.md`.
3. Create a feature branch from `cams_changes`.
4. Run the relevant existing test suite.
5. Record any pre-existing failures before changing code.
6. Search for:

   * `/settings`
   * `/transcribe`
   * `settings.html`
   * `transcribe_route_base`
   * `new-session-form`
   * `data-sidebar-settings-link`
   * `/home` inside transcriber templates
7. Make a list of every redirect that returns to settings.

Do not begin by changing CSS.

## Phase 1 — Shared context and canonical routes

1. Define workspace-section constants.
2. Extract shared shell context.
3. Add `/workspace` as an alias to the current Scribe route.
4. Add the canonical non-Scribe GET routes.
5. Initially render existing content with minimal visual changes.
6. Add role checks.
7. Add route tests before changing old routes.

Verification:

* Every canonical route returns 200 for an authorised user.
* Leader routes reject normal users.
* System-admin behaviour remains unchanged.
* Scribe still opens a consultation.

## Phase 2 — Shared template shell

1. Create the shared workspace template.
2. Render Scribe inside its main area.
3. Render Account inside its main area.
4. Confirm both use the same sidebar.
5. Add the other sections one at a time.
6. Keep the old settings page operational until all sections render correctly.

Verification after each section:

* Correct sidebar active state.
* Correct heading.
* Forms render.
* No duplicate IDs.
* No unrelated settings sections exist in the DOM.
* No JavaScript console errors.

## Phase 3 — Permanent sidebar navigation

1. Keep Create new consultation at the top.
2. Add Recent consultations for Scribe.
3. Add Back to Scribe for non-Scribe sections.
4. Add Personal group.
5. Add My Library group.
6. Add Team group with leader condition.
7. Add Sign out footer.
8. Replace the logo destination.
9. Remove the Settings footer link.

Verification:

* Sidebar collapse works.
* Sidebar resize works.
* Active item is announced with `aria-current`.
* Collapsed icons remain labelled.
* Normal users never see Team links.
* Direct leader-route access still has server checks.

## Phase 4 — Last consultation and return behaviour

1. Store active transcript ID in session storage.
2. Add Back-to-Scribe JavaScript.
3. Add brand return behaviour.
4. Add `open_recent=1` handling.
5. Add invalid/deleted transcript fallback.
6. Add no-consultation fallback.
7. Update consultation tile URLs.

Verification:

* Open an older consultation.
* Navigate to Preferences.
* Click Back to Scribe.
* Confirm the same older consultation returns.
* Confirm recent rail opens.
* Delete the remembered consultation.
* Confirm fallback to the most recent accessible consultation.
* Confirm no ownership information leaks.

## Phase 5 — Recording lock

1. Add recording state events.
2. Mark leaving-navigation controls.
3. Disable them on recording start.
4. Restore them on stop/cancel/error.
5. Add unload warning.
6. Add tooltip and disabled visuals.
7. Confirm background jobs do not trigger the lock.

Verification:

* Recording blocks all in-app routes away from the consultation.
* Uploading does not.
* Generating a note does not.
* Sidebar resize/collapse still works.
* Stopping recording restores links immediately.

## Phase 6 — Settings JavaScript and CSS extraction

1. Extract inline settings JavaScript.
2. Remove old settings mobile-menu code.
3. Namespace settings styles.
4. Extract shared shell styles from transcribe CSS.
5. Load only relevant assets per section.
6. Test every form and editor.

Do this after the structure works. Styling first will hide structural defects.

## Phase 7 — Compatibility redirects

1. Convert `/settings` GET into tab-based redirects.
2. Convert `/transcribe` GET into a redirect or compatibility alias.
3. Preserve `transcript_id`.
4. Update internal links and POST redirects.
5. Search again for old route references.
6. Keep tests for old routes.

## Phase 8 — Mobile and accessibility

1. Implement the shared sidebar drawer.
2. Add focus management.
3. Add Escape handling.
4. Add body-scroll locking.
5. Verify recent rail layering.
6. Verify recording-disabled controls with keyboard navigation.
7. Test at mobile, tablet, and desktop widths.

## Phase 9 — Cleanup

Only after all tests pass:

1. Remove unused standalone settings navigation.
2. Remove old settings mobile menu.
3. Remove `settings.html` if no route or test uses it.
4. Remove dead CSS.
5. Remove dead JavaScript.
6. Update documentation.
7. Do not remove `/home` in this change unless separately approved.

---

# 14. Automated Tests

## 14.1 Route tests

Add a focused file such as:

```text
tests/test_workspace_navigation.py
```

Test:

* `/workspace` loads for a normal user.
* `/workspace/account` loads.
* `/workspace/preferences` loads.
* Each Library route loads for an eligible user.
* Each Team route loads for a leader.
* Team routes reject a non-leader.
* Admin separation remains intact.
* Unknown consultation IDs follow existing secure behaviour.
* Deleted remembered IDs fall back safely.
* Empty users get the empty Scribe state.

## 14.2 Sidebar rendering tests

Test the returned HTML contains:

* Create new consultation on every section.
* Recent consultations on Scribe.
* Back to Scribe on non-Scribe sections.
* No generic Settings link.
* Sign out in the footer.
* Correct `aria-current`.
* Team group only for leaders.
* Brand no longer points to `/home`.

## 14.3 Compatibility tests

Test:

* `/transcribe` reaches or redirects to `/workspace`.
* `transcript_id` is preserved.
* Every known settings tab maps correctly.
* Unknown settings tab maps to Preferences.
* Old POST flows return to canonical workspace routes.

## 14.4 Form regression tests

Retain and update existing tests covering:

* Account changes
* Preference changes
* Template library
* Template editor
* Quick actions
* Smart phrases
* AI-service selection
* Team members
* Account requests
* Temporary-password display
* Dirty-form protection where testable

Do not delete tests merely because their expected URL changed. Update them to the canonical route.

## 14.5 JavaScript tests

Extend existing session-rail tests and add workspace-shell tests.

Test:

* Back to Scribe appends remembered transcript ID.
* Back to Scribe works with no stored ID.
* `open_recent=1` opens the rail.
* The query parameter is cleaned afterward.
* Recording-start event disables navigation.
* Recording-stop event restores navigation.
* Non-recording busy states do not disable it.
* Initialisers tolerate missing section markup.

## 14.6 Security tests

Test:

* A user cannot open another user’s transcript through a stored ID.
* A normal user cannot load leader routes directly.
* Hiding navigation is not the only permission control.
* Compatibility routes do not bypass authorisation.
* Redirect parameters cannot create open redirects.
* Template-path selection cannot be controlled by request input.
* CSRF remains on every POST form.
* Sign out remains POST-based if that is the current security model.

---

# 15. Manual Test Matrix

Perform this manually even when automated tests pass.

## Roles

* Regular user with consultations
* Regular user with no consultations
* Team leader
* System administrator

## Desktop scenarios

1. Open Scribe.
2. Resize sidebar.
3. Collapse and expand sidebar.
4. Open recent consultations.
5. Open an older consultation.
6. Navigate to Account.
7. Confirm sidebar remains.
8. Confirm Create new consultation remains at top.
9. Click Back to Scribe.
10. Confirm the older consultation returns.
11. Confirm recent rail opens.
12. Navigate to every Library section.
13. Edit and save a template.
14. Confirm My Templates remains active.
15. As leader, navigate through every Team section.
16. Sign out.

## Recording scenarios

1. Start microphone recording.
2. Confirm all leaving-navigation controls disable.
3. Confirm recent rail still opens.
4. Confirm sidebar still resizes/collapses.
5. Attempt browser refresh and verify warning.
6. Stop recording.
7. Confirm navigation restores.
8. Start an upload without recording.
9. Confirm navigation stays available.
10. Start note generation.
11. Confirm navigation stays available.

## Mobile scenarios

1. Open sidebar drawer.
2. Confirm focus moves into it.
3. Close with Escape.
4. Confirm focus returns to menu button.
5. Select Account and confirm drawer closes.
6. Create a consultation from Preferences.
7. Return through Back to Scribe.
8. Confirm recent rail can open without appearing behind the drawer.
9. Start recording and verify drawer navigation is disabled.

---

# 16. Acceptance Criteria

The work is complete only when all of these are true.

## Shell

* One permanent workspace sidebar is used for Scribe and all regular-user/leader sections.
* Create new consultation is always at the top.
* Settings no longer has a separate visible sidebar.
* Admin remains separate.

## Navigation

* Recent consultations appears in Scribe.
* Back to Scribe appears outside Scribe.
* Consultation selection returns to Scribe.
* Brand returns to Scribe.
* Browser Back and Forward behave normally.
* Every section has a bookmarkable URL.

## Consultation restoration

* The last-opened consultation is restored.
* Deleted or inaccessible remembered consultations fall back safely.
* Returning through Back to Scribe opens the recent rail.

## Recording

* In-app navigation away is disabled only during microphone recording.
* Refresh/close/back produces a browser warning during recording.
* Background server jobs do not block navigation.

## Roles

* Normal users see Personal and eligible Library links.
* Leaders additionally see Team links.
* Direct route access is authorised server-side.
* System administration is unaffected.

## Compatibility

* Old `/settings?tab=...` links redirect correctly.
* Old `/transcribe` links continue working.
* Existing forms and editor operations still work.
* No internal regular-user navigation points to `/home`.

## Quality

* Relevant automated tests pass.
* No JavaScript console errors occur.
* No duplicate DOM IDs occur.
* Mobile keyboard and focus behaviour works.
* No database migration is introduced unless an unexpected existing architecture requirement makes one unavoidable.

---

# 17. Common Mistakes to Avoid

1. **Do not render every settings section and hide the inactive ones with CSS.**
   Each route should load and render only its section.

2. **Do not load the complete transcript workspace for Preferences.**
   Build a lightweight shell context.

3. **Do not block navigation for generic busy states.**
   Only microphone recording locks navigation.

4. **Do not rely on hidden links for permissions.**
   Protect routes server-side.

5. **Do not rebuild settings forms.**
   Reuse the current forms and handlers.

6. **Do not leave editor pages outside the permanent shell.**
   Template and quick-action editors belong to their parent workspace section.

7. **Do not remove old routes before redirects and tests exist.**

8. **Do not store an unvalidated transcript ID and assume ownership.**

9. **Do not make the logo create a new consultation.**
   It returns to the previous or most recent one.

10. **Do not remove `/home` in this change.**
    Only stop using it from the workspace.

11. **Do not create a SPA router.**
    Normal server navigation is appropriate here.

12. **Do not combine this work with visual redesign.**
    Preserve the current design language and concentrate on information architecture.

---

# 18. Suggested Commit Breakdown

Use commits similar to:

1. `test: add workspace route and navigation expectations`
2. `refactor: split shared workspace sidebar context`
3. `feat: add canonical workspace routes`
4. `feat: render settings sections in permanent workspace shell`
5. `feat: add permanent workspace sidebar navigation`
6. `feat: restore last consultation when returning to scribe`
7. `feat: prevent workspace navigation during recording`
8. `refactor: extract shared workspace styles and scripts`
9. `feat: add mobile workspace drawer`
10. `chore: add settings and transcribe compatibility redirects`
11. `test: expand workspace role and recording coverage`
12. `docs: document permanent workspace navigation`

Each commit should leave the application runnable.

---

# 19. Developer Completion Report

When finished, report:

* Canonical routes added
* Compatibility redirects retained
* Files created
* Files substantially modified
* Old files removed
* How last-consultation restoration works
* How recording navigation locking works
* Role and authorisation behaviour
* Tests run and their results
* Manual scenarios completed
* Any remaining `/home`, `/settings`, or `/transcribe` references and why they remain
* Any deviations from this plan and the reason
