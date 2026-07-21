# Home Brief

## Purpose

This document inventories what `Home` contains today, without tying the design to current tabs, cards, modals, or other UI containers.

Goal:
- describe what information exists
- describe what can be changed
- describe who can change it
- separate normal user capabilities from leader-only capabilities

System admin flow is out of scope here because system admins are redirected away from this page into admin-specific UI.

`/home2` is a user/team-leader preview of the same Home capabilities with Admin2-style dark workspace styling. It uses the same data, form handlers, and role gates as `/home`; only route chrome and CSS differ. Its section tabs live in the left sidebar, signed-in identity sits under the sidebar title, and the speech/writing/template summary is docked at the bottom of that sidebar. Section create actions sit inside their list/panel areas instead of floating above them.

## Settings Workspace

`/settings` is a separate user/team-leader configuration workspace. `/home` remains unchanged and continues to own consultation launch/overview behavior. Settings is reachable from the transcribe sidebar, and its footer returns directly to `/transcribe` through the feather-labelled `Return to Scribe` link.

Settings uses the canonical warm `/admin` visual language without reusing the privileged admin template or context. It has a cream two-column shell, grouped sidebar navigation, serif headings, setting cards, and a responsive mobile menu.

My Templates uses a master-detail layout inside the canonical workspace library. Selecting it keeps the primary workspace sidebar and opens a contextual template sidebar grouped as `Personal` and `Team`; selecting a row opens the shared template editor or read-only preview in the detail pane. Row actions remain beside each template. Selection state uses `/workspace/library/templates?scope=personal|team&template_id=<id|new>`, does not auto-select the first template, and falls back to the empty detail state for invalid or inaccessible IDs. Embedded template forms do not stretch to fill unused pane height; free-text prompt uses the same compact row spacing and textarea height as structured section guidance. On narrow screens the list and detail become separate views with `Back to templates` navigation.

My quick actions follows same master-detail contract and exact `Personal` / `Team` grouping. Personal actions open editable forms. Same-team Team actions open read-only previews for normal users; copying snapshots latest action version into independently owned Personal action. Leaders retain Team create, edit, duplicate-in-Team, and delete controls. Selection uses `/settings?tab=quick-actions&scope=personal|team&quick_action_id=<id|new>`. Transcriber action settings links now use this canonical Settings route.

Smart phrases now use persistent Settings list/editor instead of Settings drawer creator. Personal rows support edit, duplicate, and confirmed immediate delete; `new` opens blank editor. Search stays in contextual sidebar, API errors render inline without losing typed values, and selection uses `/settings?tab=smart-phrases&smart_phrase_id=<id|new>`. Legacy `/home` drawer remains only for legacy Home workspace.

Normal-user sections:

- account details: own name, sign-in email, and password
- preferences
- My Library
  - My Templates
  - My quick actions
  - Smart phrases

Account is the default Settings section; Preferences follows it in desktop and mobile navigation.

Leader additions:

- team templates and quick actions
- AI service policy
- team members
- account requests

The page reuses existing `/home/...` form handlers and service authorization. `return_view=settings` is a closed value that returns successful or failed workflows to `/settings`; unknown values still fall back to `/home`. System admins are redirected to `/admin`.

Account changes use dedicated owner-only `/settings/account/...` handlers. Name changes update profile metadata. Email and password changes require current-password reauthentication, require TOTP when an active method exists, rotate sessions and trusted devices, and emit content-safe security audit metadata.

Normal users can see same-team templates and quick actions in My Library as read-only rows and semantic previews. They may copy either asset into Personal, producing independently editable personal config that opens inside Settings. They do not receive Team create, edit, duplicate-in-Team, or delete controls. Team management remains leader-only, and assets from other teams are never included.

Preferences keeps note length and detail visible. Model override selection and its return-to-team-default control live in a closed `Advanced` disclosure. Saving visible writing-style settings preserves any existing model override.

Leader Settings intentionally omits hard user deletion. Leaders may use existing suspend/reactivate, setup, recovery, and MFA controls. This preserves the architecture rule that team leaders may lock or deactivate users but may not fully delete them.

## Audience Split

### Shown to normal users and leaders

- signed-in identity context
  - current user display name or email
  - current team role
- quick route into consultation workspace
- quick route to sign out
- short guided-tour/help content
- transient success/error status messaging
- high-level service summary
  - active speech service label when present
  - active writing assistant label when present
  - resolved model shown for writing assistant
- saved template count

### Leader-only additions

- team AI service configuration
- team member management
- team-scoped template management
- team-scoped quick action management
- account-request review

## Information Inventory

### Identity and session context

Contained information:
- signed-in user name or email
- current role label

User can change:
- nothing here directly except ending the session

How:
- sign out action

Access:
- user and leader

### Consultation launch context

Contained information:
- direct action to open consultation workspace
- readiness guidance when consultation capture is blocked or limited
- missing-speech-service guidance
- leader contact hint for non-leaders when setup is incomplete

User can change:
- nothing in the messaging itself

How:
- open consultation workspace
- complete the missing setup elsewhere if permitted

Access:
- user and leader

### Personal writing-assistant preference

Contained information:
- currently active writing assistant
- team default model
- models the current user is allowed to choose from
- saved personal model override if one exists

User can change:
- personal preferred LLM model
- clear personal override back to team default
- personal note length and detail preferences

How:
- choose one allowed model from the team-approved list
- save preference
- clear preference
- open Advanced only when changing model selection

Access:
- user and leader

### Template library

Contained information:
- personal templates
- leader-visible team templates
- per template:
  - name
  - scope: personal or team
  - note mode: freeform or structured
  - active/inactive availability
  - description in edit context
  - full prompt text in edit context
  - structured section guidance in edit context when applicable

User can change:
- create, edit, activate/deactivate, and delete personal templates
- copy a same-team template into an independently editable personal template

Leader can also change:
- create, edit, activate/deactivate, and delete team templates

How:
- create new template metadata and prompt guidance
- revise existing template metadata and prompt guidance
- remove template permanently

Access:
- user: personal templates plus read-only same-team templates
- leader: personal + team templates

### Quick action library

Contained information:
- personal quick actions
- same-team quick actions
- per quick action:
  - name
  - scope: personal or team
  - active/inactive availability
  - description in edit context
  - prompt text in edit context

User can change:
- create, edit, activate/deactivate, and delete personal quick actions
- review same-team Team quick actions and copy them into independently owned Personal actions

Leader can also change:
- create, edit, activate/deactivate, and delete team quick actions

How:
- create new quick action metadata and prompt text
- revise existing quick action metadata and prompt text
- remove quick action permanently

Access:
- user: personal quick actions plus read-only same-team quick actions
- leader: personal + team quick actions

### Team speech-to-text policy

Contained information:
- whether a team speech service is active
- active provider label
- active model override or provider default
- active language override or provider default
- all admin-provisioned selectable team speech services
- per selectable speech service:
  - label
  - adapter kind
  - default model
  - default language
  - endpoint details

Leader can change:
- active team speech service
- optional model override
- optional language override
- clear active team speech service

How:
- choose one admin-provisioned speech service
- optionally override model
- optionally override language
- save selection
- clear selection

Access:
- leader only

### Team writing-assistant policy

Contained information:
- whether a team writing assistant is active
- active provider label
- team default model
- allowed-model subset
- all admin-provisioned selectable LLM providers for the team
- per selectable provider:
  - label
  - adapter kind
  - available model list
  - endpoint details

Leader can change:
- active team writing assistant provider
- allowed model subset
- default model chosen from the allowed subset
- clear active team writing assistant

How:
- choose one admin-provisioned writing assistant
- choose which provider models are allowed for team members
- choose the default model from that allowed set
- save selection
- clear selection

Access:
- leader only

### Team member directory and account controls

Contained information:
- current team name
- manageable users in the leader's team
- per user:
  - avatar initial
  - full name or email
  - email address
  - team role
  - account status

Leader can change:
- create user
- choose new user role
- choose new user starting status
- choose MFA required flag for new user
- suspend active user
- reactivate suspended/disabled user
- delete user

How:
- enter new-user identity and temporary password
- choose role/status/MFA requirement
- save creation
- use the per-user actions menu for suspend/reactivate/setup link/password recovery/MFA reset/account recovery/email/delete

Access:
- leader only

Notes:
- delete here is high impact because it deletes owned transcript-derived content too
- per-user actions menu is layered above the member list, closes when clicking outside, and auto-closes after a short unhovered idle period

### Incoming account requests

Contained information:
- pending and processed requests visible to leader scope
- per request:
  - requested name
  - requested email
  - requested team name
  - free-text request details
  - request status

Leader can change:
- approve request
- reject request
- assign team role on approval
- set temporary password on approval
- add review notes on approval or rejection

How:
- enter approval details and approve
- enter rejection notes and reject

Access:
- leader only

## Capability Summary By Role

### Normal user

Can:
- open consultation workspace
- sign out
- read current service summaries
- save or clear personal LLM model preference
- create, edit, activate/deactivate, and delete personal templates
- create, edit, activate/deactivate, and delete personal quick actions

Cannot:
- change team STT policy
- change team LLM policy
- manage team members
- review access requests
- create or edit team templates
- create or edit team quick actions

### Leader

Can do everything a normal user can, plus:
- change team speech-service selection
- change team writing-assistant selection and model policy
- clear team AI selections
- create, suspend, reactivate, and delete team users in leader scope
- approve or reject account requests
- create, edit, activate/deactivate, and delete team templates
- create, edit, activate/deactivate, and delete team quick actions

## Design Constraints Implied By Current Data

- `Home` mixes:
  - session context
  - launch actions
  - personal preferences
  - personal asset libraries
  - leader policy controls
  - team people admin
  - access triage
- some data is summary-only
- some data is editable metadata
- some data is destructive or high-impact
- AI service configuration is metadata/policy, not content access
- transcript-derived content is not shown here

Useful design split:
- personal workspace controls
- reusable assets
- leader policy
- people/access admin

## Transcribe Color Scheme

### Actual code tokens

```css
/* app/templates/transcribe/_head_assets.html */
:root {
  --bg: #FAF8F5;
  --fg: #1A202C;
  --muted: #718096;
  --accent: #1D4F5E;
  --accent-soft: #3D7A8C;
  --card: #FFFFFF;
  --border: #E2DED6;
  --success: #38A169;
  --warning: #D69E2E;
  --error: #C53030;
}

/* tailwind-config colors used by transcribe */
colors: {
  cream: '#FAF8F5',
  parchment: '#F5F1EB',
  stone: '#E8E4DD',
  slate: '#2D3748',
  ink: '#1A202C',
  teal: {
    deep: '#1D4F5E',
    muted: '#3D7A8C',
    soft: '#5BA3B5',
    pale: '#E0F2F5',
  },
  sage: '#7A9E7E',
  amber: '#D4A574',
  coral: '#D97D54',
  error: '#C53030',
  success: '#38A169',
  warning: '#D69E2E',
}
```

### Semantic pseudocode

```text
PAGE_BACKGROUND = cream
SECONDARY_SURFACE = parchment
PRIMARY_SURFACE = white
DEFAULT_BORDER = warm light stone

PRIMARY_TEXT = ink
SECONDARY_TEXT = muted slate
STRONG_NEUTRAL_TEXT = slate

PRIMARY_ACTION = teal.deep
PRIMARY_ACTION_HOVER = teal.muted
SOFT_ACCENT = teal.soft
ACCENT_WASH = teal.pale

SUCCESS_STATE = success green
WARNING_STATE = warning amber
ERROR_STATE = error red

SELECTION_HIGHLIGHT = amber wash
ACTIVE_SESSION_HIGHLIGHT = deep teal wash
```

## Recommended Use Of This Brief

Use this document as a UI-content map:
- not “where things are now”
- but “what information exists and who can act on it”

That should make it easier to redesign the page without preserving accidental current layout decisions.
