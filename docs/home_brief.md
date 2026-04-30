# Home Brief

## Purpose

This document inventories what `Home` contains today, without tying the design to current tabs, cards, modals, or other UI containers.

Goal:
- describe what information exists
- describe what can be changed
- describe who can change it
- separate normal user capabilities from leader-only capabilities

System admin flow is out of scope here because system admins are redirected away from this page into admin-specific UI.

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

How:
- choose one allowed model from the team-approved list
- save preference
- clear preference

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

Leader can also change:
- create, edit, activate/deactivate, and delete team templates

How:
- create new template metadata and prompt guidance
- revise existing template metadata and prompt guidance
- remove template permanently

Access:
- user: personal templates only
- leader: personal + team templates

### Quick action library

Contained information:
- personal quick actions
- leader-visible team quick actions
- per quick action:
  - name
  - scope: personal or team
  - active/inactive availability
  - description in edit context
  - prompt text in edit context

User can change:
- create, edit, activate/deactivate, and delete personal quick actions

Leader can also change:
- create, edit, activate/deactivate, and delete team quick actions

How:
- create new quick action metadata and prompt text
- revise existing quick action metadata and prompt text
- remove quick action permanently

Access:
- user: personal quick actions only
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
