## Overall implementation plan: redesigned Follow Ups tab

Build the redesigned Follow Ups tab around this product model:

```text
Write follow-up context → optionally apply a quick action → preview what will be sent → generate → review selected output
```

The key change from the current page is that **free-text follow-up context becomes the primary input**, and quick actions become optional saved formats/pathways.

---

## Relevant codebase areas

From the repo, the work is centered in the transcribe workspace:

| File                                         | Role                                                                                                                          |
| -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `app/templates/transcribe/_workspace.html`   | Main transcribe workspace template, including the existing Follow Ups tab markup.                                             |
| `app/templates/transcribe/_head_assets.html` | Inline CSS for the transcribe workspace; good place for the new layout classes.                                               |
| `app/static/js/transcribe/app.js`            | Main workspace JS; already owns follow-up inputs, quick-action select/trigger, dictation, selected follow-up document state.  |
| `app/static/js/transcribe/documents.js`      | Document navigation/rendering helper; already renders follow-up history and selected follow-up state.                         |

The existing code already has the functional hooks needed for generation:

```text
data-quick-action-select
data-quick-action-context-input
data-run-quick-action-trigger
data-followup-prompt-input
data-generate-followup-trigger
data-followup-history
data-latest-followup-output
data-followup-llm-request-slot
data-followup-redaction-debug-slot
```

The agent should preserve these hooks or provide compatibility shims.

---

# 1. Target UX

## Page structure

The Follow Ups tab should become a three-column workspace:

```text
┌──────────────────────┬────────────────────────────────┬──────────────────────────────┐
│ Quick Actions         │ Builder with combined preview  │ Generated Follow Up           │
│                       │                                │                              │
│ Search                │ 1. Write follow-up context     │ Selected generated output     │
│ All quick actions     │ 2. Optional quick action       │ Copy / LLM request / Delete   │
│                       │ 3. Prompt preview              │ Recent for this transcript    │
└──────────────────────┴────────────────────────────────┴──────────────────────────────┘
```

## Main interaction model

The user should be able to generate a follow-up in four states:

| Context text | Quick action | Expected behavior                                                          |
| ------------ | ------------ | -------------------------------------------------------------------------- |
| Empty        | None         | Generate disabled; preview says “Nothing to send yet.”                     |
| Filled       | None         | Generate from follow-up context only.                                      |
| Empty        | Selected     | Generate from selected quick action + current consultation note.           |
| Filled       | Selected     | Generate from selected quick action + context + current consultation note. |

This is the most important rule for the implementation.

---

# 2. Left panel: Quick Actions

## Requirements

Show all available quick actions in a searchable list.

Each quick action card should include:

* icon
* name
* description
* selected state/checkmark when active

Example:

```text
2wwCRC
2WW Colorectal Referral
```

```text
Patient follow-up message
Draft a short patient-facing follow-up message.
```

```text
Referral letter
Draft a referral letter from the consultation.
```

## Ordering

Use all quick actions.

Preferred ordering:

1. Most-used first, **only if existing schema/data supports usage count or usage timestamp**.
2. Otherwise:

   * default quick action first if already available via existing app preferences
   * favorites next if already available
   * alphabetical after that

Do not add a DB migration purely for “most used” in this first pass.

## Search behavior

Typing in the quick-action search box filters cards by:

* name
* description

If no result:

```text
No quick actions match your search.
```

## Selecting a quick action

Clicking a card:

* sets `data-quick-action-select`
* highlights the selected card
* updates builder section 2
* updates prompt preview

---

# 3. Center panel: Builder with combined preview

Use this exact hierarchy.

## Header

```text
Option 3 — Builder with combined preview
```

Right side:

```text
Learn more
```

The “Learn more” can be non-functional initially, or link to a future help/docs page.

---

## Section 1: Write your follow-up context

Label:

```text
1  WRITE YOUR FOLLOW-UP CONTEXT
```

Helper:

```text
Describe what you want the follow-up to say, include, or achieve.
```

Textarea:

* primary input
* around 160–200px tall
* max length 2000 or 4000; choose one and apply consistently
* placeholder:

```text
Please add details to include in the follow-up...
```

Right-side action:

```text
Record context
```

This should target this textarea.

### Data mapping

This textarea should sync into both existing generation paths:

* For quick-action generation, sync to `data-quick-action-context-input`.
* For free-text generation, sync to `data-followup-prompt-input`.

That allows one visual composer to work with both existing flows.

---

## Section 2: Optional quick action

Label:

```text
2  OPTIONAL QUICK ACTION
```

Helper:

```text
Apply a saved format or pathway to shape the output.
```

### Empty state

When no quick action is selected:

```text
No quick action selected
Generate from your context alone, or choose a quick action to apply a saved structure.

[Choose quick action]
```

Button behavior:

* focuses the quick-action search/list in the left panel
* optionally pulse/highlight the left panel

It should not navigate away.

### Selected state

When a quick action is selected:

```text
Patient follow-up message                         [Remove]
Draft a short patient-facing follow-up message.
```

Button should be **Remove**, not Delete.

Reason: it removes the quick action from this generated follow-up; it does not delete the saved quick action.

Remove behavior:

* clears `data-quick-action-select`
* clears selected card state
* updates section 2 to empty state
* updates preview

---

## Section 3: Prompt preview

Label:

```text
3  PROMPT PREVIEW — What will be sent
```

Right side:

```text
Live preview
```

Preview states:

### No context, no quick action

```text
Nothing to send yet
Write follow-up context or choose a quick action.
```

### Context only

```text
Follow-up context only
The follow-up will be generated from your instructions.

No quick action selected — the output will be based on your follow-up context only.
```

### Quick action only

```text
Quick action only
The follow-up will use [quick action name] and the current consultation note.

Add context above if you want to steer the output further.
```

### Context + quick action

```text
Context + [quick action name]
Your instructions will be combined with the selected quick action and the current consultation note.

Both sections are used together to generate the follow-up.
```

---

## Bottom buttons

```text
[Generate] [Clear]
```

### Generate

Rules:

* disabled when no context and no quick action
* if quick action selected:

  * set hidden select
  * sync context into quick-action context field
  * click existing `data-run-quick-action-trigger`
* if no quick action:

  * sync context into custom prompt field
  * click existing `data-generate-followup-trigger`

### Clear

Clears composer state only:

* clears visual context textarea
* clears hidden quick-action context input
* clears hidden custom prompt input
* removes selected quick action
* resets preview
* does **not** delete generated follow-ups
* does **not** clear the right output panel

---

# 4. Right panel: Generated Follow Up

Show only the selected generated follow-up.

## Header

```text
GENERATED FOLLOW UP
```

Actions:

```text
Copy    LLM request    Delete
```

## Output card

States:

### Empty

```text
No follow-up generated yet.
Write your follow-up context, optionally choose a quick action, then generate.
```

### Queued

```text
Your follow-up is waiting to be written.
```

### Processing

```text
Your follow-up is being written.
```

### Ready

Show status badge, timestamp, and body.

```text
READY   2026-05-13T16:15:25...
[generated text]
```

### Failed

```text
The follow-up could not be created: [error]
```

## Copy

Copies only the generated body text, not timestamp/status/title.

## LLM request

Toggle/display the existing LLM request panel for the selected generated document.

## Delete

Deletes the selected generated follow-up using the existing generated-document delete behavior.

Should confirm first if the existing flow does not already confirm.

---

# 5. Recent list

Recent list belongs in the right panel under the output.

It should include **only follow-ups for the current transcript**, not global history.

Each item:

```text
Patient follow-up message       2026-05-13T16:13...
Referral letter                 2026-05-13T16:12...
2wwCRC                          2026-05-13T12:09...
```

Click behavior:

* selects that generated follow-up
* updates right output panel
* does **not** overwrite the builder composer

Implementation can reuse `workspaceFollowupDocuments` and `selectedFollowupDocumentId`.

---

# 6. Dictation behavior

Existing dictation currently supports context. Extend or reuse it so **Record context** inserts into the new visual section 1 textarea.

Expected behavior:

1. User clicks `Record context`.
2. Existing dictation flow starts.
3. Result text is appended to the follow-up context textarea.
4. Textarea syncs into hidden generation fields.
5. Preview updates.

Append behavior is preferable to replace:

```text
Existing text

Dictated text
```

Ambiguity: if the existing dictation code is tightly coupled to `data-quick-action-context-input`, the agent can keep that hidden input as the real target and mirror the value into the new visible textarea.

---

# 7. Suggested implementation approach

## Preferred: template-first implementation

Modify `_workspace.html` so the Follow Ups tab renders the new layout directly.

Pros:

* cleaner DOM
* more maintainable
* less risk from hidden duplicate UI
* easier to test

Cons:

* larger Jinja edit

### Work in `_workspace.html`

Replace the current Follow Ups tab panel internals with:

```text
followup-v2-shell
  followup-v2-quick-actions
  followup-v2-builder
  followup-v2-output
```

Keep existing hidden controls where needed:

```html
<select data-quick-action-select class="sr-only">...</select>
<textarea data-quick-action-context-input hidden></textarea>
<textarea data-followup-prompt-input hidden></textarea>
<button data-run-quick-action-trigger hidden></button>
<button data-generate-followup-trigger hidden></button>
```

Or keep them visually hidden if the existing JS requires them.

## Alternative: JS overlay implementation

Keep `_workspace.html` mostly unchanged and build the new UI dynamically in JS, hiding the old Follow Ups tab.

Pros:

* faster to implement
* lower Jinja risk
* can reuse current hidden elements

Cons:

* more fragile
* harder to test
* duplicate DOM
* not ideal long term

I recommend **template-first** if the agent has time.

---

# 8. CSS plan

Add a v2 CSS block in `_head_assets.html`.

Class family:

```text
.followup-v2-shell
.followup-v2-panel
.followup-v2-quick-actions
.followup-v2-action-card
.followup-v2-action-card.is-selected
.followup-v2-builder
.followup-v2-step
.followup-v2-context
.followup-v2-selected-action
.followup-v2-empty-action
.followup-v2-preview
.followup-v2-output
.followup-v2-recent
```

Layout:

```css
.followup-v2-shell {
  display: grid;
  grid-template-columns: minmax(17rem, .85fr) minmax(28rem, 1.45fr) minmax(31rem, 1.55fr);
  gap: .9rem;
  height: 100%;
  padding: .9rem;
  overflow: hidden;
}
```

Responsive:

* Below around `1250px`, allow the right output panel to wrap below.
* Below around `860px`, stack all panels vertically.

---

# 9. JS plan

Most JS likely belongs in `app/static/js/transcribe/app.js`, because that file already owns core transcribe state and follow-up controls. `documents.js` should remain focused on rendering selected/generated documents where possible.

## Add DOM refs

Add refs for:

```text
data-followup-v2-search
data-followup-v2-action-card
data-followup-v2-context
data-followup-v2-count
data-followup-v2-selected-action
data-followup-v2-preview
data-followup-v2-generate
data-followup-v2-clear
data-followup-v2-record-context
data-followup-v2-copy
data-followup-v2-delete
data-followup-v2-llm
data-followup-v2-recent-item
```

## State

Add local state:

```js
let selectedFollowupQuickActionId = '';
```

Do not confuse this with selected generated follow-up document ID.

## Core functions

Implement:

```js
syncFollowupContextInputs()
selectFollowupQuickAction(id)
clearFollowupQuickAction()
renderFollowupQuickActionCards()
renderFollowupSelectedAction()
renderFollowupPromptPreview()
renderFollowupGenerateEnabled()
clearFollowupComposer()
generateFollowupFromComposer()
```

## Generation function

Pseudo-code:

```js
function generateFollowupFromComposer() {
  const context = followupContextInput.value.trim();
  const quickActionId = selectedFollowupQuickActionId;

  hiddenQuickActionContextInput.value = context;
  hiddenPromptInput.value = context;

  if (quickActionId) {
    quickActionSelect.value = quickActionId;
    runQuickActionTrigger.click();
  } else {
    generateFollowupTrigger.click();
  }
}
```

Important: confirm whether the backend expects quick-action context and custom prompt to come from different fields. Existing code likely already handles both.

---

# 10. Document rendering changes

`documents.js` already has responsibilities around follow-up document selection/rendering. It should be updated so the new right panel stays in sync with:

```text
workspaceFollowupDocuments
selectedFollowupDocumentId
latestFollowupOutput
followupHistory
followupLlmRequestSlot
```

If keeping old rendering:

* The new right panel can be the primary visible UI.
* Existing hidden `data-followup-history` can still exist for compatibility.
* When `renderSelectedFollowup()` runs, also update the v2 output panel and recent list.

Expected behavior:

```text
renderSelectedFollowup()
  updates legacy latestFollowupOutput
  updates v2 output card
  updates v2 recent list
  renders LLM request panel
```

---

# 11. Backend changes

Try to avoid backend changes.

Existing flows should be sufficient:

* Quick action generation
* Custom follow-up generation
* Generated document selection/history
* Delete generated document
* LLM request display

Backend may only need changes if:

1. Current custom follow-up endpoint cannot accept the same context field.
2. Current quick-action endpoint requires quick action and context in a way that conflicts with the new builder.
3. Usage-count sorting for quick actions is desired and not available.

For first pass, do not add usage-count schema.

---

# 12. Ambiguities still open

## 1. Textarea max length

We discussed both `2000` and `4000`.

Recommendation:

* Use `2000` in the visible builder for a cleaner prompt UX.
* If backend already expects `4000`, either:

  * keep `4000`, or
  * enforce `2000` client-side only and send normally.

Agent should check existing validation before changing.

## 2. “Popular” badge

Only show `Popular` / `Most used` if backed by actual data.

If no usage data exists, do not fake it.

## 3. Default quick action

Question: should a default/favorite quick action auto-select on page load?

Recommendation: no. Start with no quick action selected unless:

* the user explicitly selected one earlier in this page session, or
* there is already a persisted default and product wants to honor it.

The new mental model makes context primary, so no auto-selection is cleaner.

## 4. “Learn more”

Can be non-functional or hidden for now.

If included, decide later where it links.

## 5. Dictation append vs replace

Recommendation: append dictated text to existing context.

But if current dictation code replaces text, agent may keep replacement initially and improve later.

## 6. Delete confirmation

If existing generated-document delete flow already confirms, reuse it.

If not, add confirmation.

## 7. Generated output while composer is edited

Selecting a recent follow-up should not overwrite the composer.

If the user edits the builder while viewing an older output, that is acceptable. The builder is for the next generation; right panel is selected result history.

---

# 13. Acceptance criteria

The implementation is done when:

* Follow Ups tab uses the new three-column layout.
* Primary center section is **Write your follow-up context**.
* Quick action is optional and removable.
* Prompt preview accurately changes for all four input states.
* Generate works with:

  * context only
  * quick action only
  * context + quick action
* Clear resets composer only.
* Quick-action search and selection work.
* Recent list is current-transcript only.
* Right panel shows only the selected generated follow-up.
* Copy, Delete, and LLM request still work.
* Dictation can fill the new context textarea.
* Clinical Note and Transcript tabs still work.
* No JS errors when:

  * no active transcript
  * no quick actions
  * no follow-up documents
  * LLM unavailable
  * generated document failed/queued/processing.
