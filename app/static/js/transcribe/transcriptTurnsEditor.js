import { csrfFetch } from '../csrf.js';
import { createAutosaveController } from './autosaveController.js';

const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[character]));

export const codepointToUtf16 = (text, index) => Array.from(text).slice(0, Math.max(0, index)).join('').length;

export function projectStructuredTurns(turns, overrides = new Map()) {
  return (Array.isArray(turns) ? turns : []).map((turn) => overrides.has(turn.id) ? overrides.get(turn.id) : turn.text).join('\n');
}

function turnTime(turn) {
  if (!Number.isFinite(turn.start_ms)) return '';
  const seconds = Math.max(0, Math.floor(turn.start_ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function speakerTone(turn) {
  const match = String(turn.speaker_label || '').match(/^Speaker ([1-4])$/);
  return match ? match[1] : 'neutral';
}

export function createTranscriptTurnsEditor({
  root,
  getTranscriptId,
  onToast = () => {},
  onCanonicalText = () => {},
  onEdited = () => {},
  onSourceSaved = () => {},
  fetcher = csrfFetch,
} = {}) {
  let turns = [];
  let revisionId = null;
  let activeTranscriptId = null;
  let editable = false;
  let masked = false;
  let piiEntities = [];
  let editingTurnId = null;
  let dirtyTexts = new Map();
  let statusNode = null;
  let retryNode = null;
  let failed = false;
  let remoteConflict = false;
  let actionPending = false;
  let piiRevisionId = null;
  let lastConfirmedSource = null;
  const supersededRevisions = new Set();

  const isDirty = () => dirtyTexts.size > 0;
  const turnById = (id) => turns.find((turn) => turn.id === id);
  const canonicalText = () => projectStructuredTurns(turns, dirtyTexts);
  const updateCanonical = () => onCanonicalText(canonicalText());

  const setStatus = (message, kind = 'saved') => {
    statusNode ||= root?.parentElement?.querySelector('[data-turn-save-status]') || null;
    retryNode ||= statusNode?.querySelector('[data-turn-retry]') || null;
    if (!statusNode) return;
    const messageNode = statusNode.querySelector('[data-turn-save-message]');
    if (messageNode) messageNode.textContent = message;
    else statusNode.textContent = message;
    statusNode.dataset.state = kind;
    statusNode.hidden = !message;
    if (retryNode) retryNode.hidden = kind !== 'error';
  };

  const mergeServerTurns = (nextTurns) => {
    const serverTurns = Array.isArray(nextTurns) ? nextTurns : [];
    const serverIds = new Set(serverTurns.map((turn) => turn.id));
    const orphaned = [];
    for (const id of dirtyTexts.keys()) {
      if (!serverIds.has(id)) {
        const previous = turnById(id);
        if (previous) orphaned.push({ ...previous, speaker_id: null, speaker_label: '?', text: dirtyTexts.get(id) });
        remoteConflict = true;
      }
    }
    if (orphaned.length) setStatus('A transcript turn changed elsewhere. Your edits are kept here; reload before retrying.', 'conflict');
    turns = [...serverTurns.map((turn) => ({ ...turn, text: dirtyTexts.has(turn.id) ? dirtyTexts.get(turn.id) : turn.text })), ...orphaned];
    updateCanonical();
  };

  const applyResponse = (payload, ownWrite = false) => {
    const transcript = payload?.transcript || payload;
    if (!transcript || !Array.isArray(transcript.structured_turns)) return false;
    const nextRevisionId = transcript.structured_revision_id || revisionId;
    if (isDirty() && !ownWrite && revisionId && nextRevisionId !== revisionId) {
      remoteConflict = true;
      setStatus('This transcript changed elsewhere. Your edits are kept here; reload before retrying.', 'conflict');
      return true;
    }
    if (ownWrite && revisionId && nextRevisionId !== revisionId) supersededRevisions.add(revisionId);
    if (!isDirty() || ownWrite) revisionId = nextRevisionId;
    if (!remoteConflict || ownWrite) mergeServerTurns(transcript.structured_turns);
    editable = Boolean(transcript.structured_editable);
    if (ownWrite) {
      remoteConflict = false;
      piiRevisionId = null;
      lastConfirmedSource = transcript;
      onSourceSaved(transcript);
    }
    return true;
  };

  const maskedTurnText = (turn, value) => {
    if (!masked) return value;
    const codepoints = Array.from(value);
    const ranges = [];
    if (isDirty() || piiRevisionId !== revisionId) return value.replace(/\S/gu, '•');
    const canonicalCodepoints = Array.from(projectStructuredTurns(turns));
    for (const entity of piiEntities) {
      if (entity?.dismissed) continue;
      if (entity?.source === 'detected' && Number.isInteger(entity.start_index) && Number.isInteger(entity.end_index)
        && Number.isInteger(turn.start_index) && Number.isInteger(turn.end_index)
        && entity.start_index < turn.end_index && entity.end_index > turn.start_index) {
        if (canonicalCodepoints.slice(entity.start_index, entity.end_index).join('') !== entity.value) continue;
        const localStart = Math.max(0, entity.start_index - turn.start_index);
        const localEnd = Math.min(codepoints.length, entity.end_index - turn.start_index);
        ranges.push([localStart, localEnd]);
      } else if (entity?.source === 'manual' && entity.value) {
        const needle = Array.from(entity.value);
        for (let index = 0; needle.length && index <= codepoints.length - needle.length; index += 1) {
          if (codepoints.slice(index, index + needle.length).join('').toLowerCase() === entity.value.toLowerCase()) ranges.push([index, index + needle.length]);
        }
      }
    }
    for (const [start, end] of ranges) {
      for (let index = Math.max(0, start); index < Math.min(codepoints.length, end); index += 1) {
        if (!/\s/u.test(codepoints[index])) codepoints[index] = '•';
      }
    }
    return codepoints.join('');
  };

  const highlightedTurnHtml = (turn, value) => {
    if (dirtyTexts.has(turn.id)) return escapeHtml(maskedTurnText(turn, value));
    const codepoints = Array.from(value);
    const ranges = [];
    const canonicalCodepoints = Array.from(projectStructuredTurns(turns));
    for (const entity of piiEntities) {
      if (entity?.dismissed) continue;
      if (entity?.source === 'detected' && Number.isInteger(entity.start_index) && Number.isInteger(entity.end_index)
        && Number.isInteger(turn.start_index) && Number.isInteger(turn.end_index)
        && entity.start_index < turn.end_index && entity.end_index > turn.start_index) {
        if (canonicalCodepoints.slice(entity.start_index, entity.end_index).join('') !== entity.value) continue;
        const localStart = Math.max(0, entity.start_index - turn.start_index);
        const localEnd = Math.min(codepoints.length, entity.end_index - turn.start_index);
        ranges.push([localStart, localEnd]);
      } else if (entity?.source === 'manual' && entity.value) {
        const needle = Array.from(entity.value);
        for (let index = 0; needle.length && index <= codepoints.length - needle.length; index += 1) {
          if (codepoints.slice(index, index + needle.length).join('').toLowerCase() === entity.value.toLowerCase()) ranges.push([index, index + needle.length]);
        }
      }
    }
    ranges.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
    const parts = [];
    let cursor = 0;
    for (const [start, end] of ranges) {
      if (start < cursor) continue;
      parts.push(escapeHtml(codepoints.slice(cursor, start).join('')));
      parts.push(`<mark class="transcript-pii-highlight">${escapeHtml(masked ? codepoints.slice(start, end).join('').replace(/\S/gu, '•') : codepoints.slice(start, end).join(''))}</mark>`);
      cursor = end;
    }
    parts.push(escapeHtml(codepoints.slice(cursor).join('')));
    return parts.join('') || '&nbsp;';
  };

  const saveDirtyTurns = async ({ keepalive = false } = {}) => {
    const transcriptId = activeTranscriptId || getTranscriptId();
    if (!transcriptId || !revisionId) return null;
    if (remoteConflict) return null;
    const pending = [...dirtyTexts.entries()];
    if (!pending.length) return null;
    setStatus('Saving transcript edits…', 'saving');
    for (const [turnId, text] of pending) {
      const response = await fetcher(`/api/v1/transcripts/${encodeURIComponent(transcriptId)}/turns/${encodeURIComponent(turnId)}`, {
        method: 'PATCH',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        keepalive,
        body: JSON.stringify({ expected_version_id: revisionId, text }),
      });
      if (response.status === 409) {
        failed = true;
        setStatus('This transcript changed elsewhere. Your edits are kept here; reload before retrying.', 'conflict');
        return null;
      }
      if (!response.ok) throw new Error(`Could not save transcript edits (${response.status}).`);
      const payload = await response.json();
      const returned = payload?.transcript || payload;
      if (!returned?.structured_revision_id) throw new Error('The server did not confirm the saved transcript revision.');
      const latestInput = dirtyTexts.get(turnId);
      if (latestInput === text) dirtyTexts.delete(turnId);
      applyResponse(returned, true);
      if (latestInput !== text) dirtyTexts.set(turnId, latestInput);
    }
    failed = false;
    setStatus(isDirty() ? 'More edits pending…' : 'Transcript edits saved.', isDirty() ? 'saving' : 'saved');
    updateCanonical();
    if (!root.contains(document.activeElement)) renderTurns();
    return { revision_id: revisionId };
  };

  const autosave = createAutosaveController({
    save: saveDirtyTurns,
    isDirty,
    onFailure: () => {
      failed = true;
      setStatus('Could not save. Your edits are kept here; select Retry to save again.', 'error');
    },
    onSaved: () => { failed = false; },
  });

  const renderTurns = () => {
    if (!root) return;
    if (!Array.isArray(turns) || !turns.length) {
      root.innerHTML = `<div class="transcript-turns-empty">No transcript text yet.</div>${editable && !masked ? '<button type="button" class="transcript-turns-add" data-turn-add>Add unattributed text</button>' : ''}`;
      root.hidden = false;
      return;
    }
    const canEdit = editable && !masked;
    const groups = [];
    let previousScope = null;
    let scopeNumber = 0;
    for (const turn of turns) {
      if (turn.scope_id !== previousScope) {
        scopeNumber += 1;
        groups.push({ scope_id: turn.scope_id, number: scopeNumber, turns: [] });
        previousScope = turn.scope_id;
      }
      groups[groups.length - 1].turns.push(turn);
    }
    const showScopeHeadings = groups.length > 1;
    root.innerHTML = `
      <div class="transcript-turns-editor" data-transcript-turn-list>
        ${groups.map((group) => `
          <section class="transcript-turn-scope" data-turn-scope="${escapeHtml(group.scope_id)}">
            ${showScopeHeadings ? `<h3 class="transcript-turn-scope__heading">Recording ${group.number}${group.number > 1 ? ' · speakers not matched to previous recording' : ''}</h3>` : ''}
            ${group.turns.map((turn) => {
              const rawValue = dirtyTexts.has(turn.id) ? dirtyTexts.get(turn.id) : turn.text;
              const value = maskedTurnText(turn, rawValue);
              const startTime = turnTime({ start_ms: turn.start_ms });
              const endTime = turnTime({ start_ms: turn.end_ms });
              return `<article class="transcript-turn" data-turn-id="${escapeHtml(turn.id)}" data-speaker-tone="${speakerTone(turn)}">
                <header class="transcript-turn__meta"><span class="transcript-turn__speaker">${escapeHtml(turn.speaker_label || '?')}</span>${startTime ? `<time title="Original audio interval; edited words are not word-timed">${escapeHtml(startTime)}${endTime ? `–${escapeHtml(endTime)}` : ''}</time>` : ''}${turn.edited ? '<span class="transcript-turn__edited">Edited</span>' : ''}
                  ${canEdit ? `<details class="transcript-turn__actions"><summary aria-label="Turn actions" title="Turn actions">⋯</summary><button type="button" data-turn-delete="${escapeHtml(turn.id)}">Delete turn</button></details>` : ''}
                </header>
                ${canEdit && editingTurnId === turn.id
                  ? `<textarea class="transcript-turn__text" data-turn-text="${escapeHtml(turn.id)}" aria-label="${escapeHtml(turn.speaker_label || '?')} transcript turn" rows="${Math.max(2, Math.min(12, String(value).split('\n').length + 1))}">${escapeHtml(value)}</textarea>`
                  : canEdit
                    ? `<button type="button" class="transcript-turn__display" data-turn-display="${escapeHtml(turn.id)}" aria-label="Edit ${escapeHtml(turn.speaker_label || '?')} transcript turn">${highlightedTurnHtml(turn, value).replace(/\n/g, '<br>')}</button>`
                    : `<div class="transcript-turn__display">${masked ? escapeHtml(value).replace(/\n/g, '<br>') : highlightedTurnHtml(turn, value).replace(/\n/g, '<br>')}</div>`}
              </article>`;
            }).join('')}
          </section>`).join('')}
        ${canEdit ? '<button type="button" class="transcript-turns-add" data-turn-add>Add unattributed text</button>' : ''}
      </div>`.trim();
    root.hidden = false;
    statusNode = root.parentElement?.querySelector('[data-turn-save-status]') || statusNode;
  };

  const persistAction = async (url, method, body) => {
    const transcriptId = activeTranscriptId || getTranscriptId();
    if (!transcriptId || !revisionId) return;
    if (isDirty() && !(await autosave.flush())) return;
    const response = await fetcher(url(transcriptId), {
      method,
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_version_id: revisionId, ...body }),
    });
    if (response.status === 409) {
      failed = true;
      setStatus('This transcript changed elsewhere. Reload before making further changes.', 'conflict');
      return;
    }
    if (!response.ok) throw new Error(`Could not update transcript (${response.status}).`);
    const payload = await response.json();
    if (!applyResponse(payload, true)) throw new Error('The server did not return the updated transcript.');
    setStatus('Transcript edits saved.', 'saved');
    renderTurns();
    return { lastTurnId: turns.at(-1)?.id || null };
  };

  root?.addEventListener('input', (event) => {
    const input = event.target instanceof HTMLTextAreaElement ? event.target : null;
    if (!input?.matches('[data-turn-text]')) return;
    const id = input.dataset.turnText;
    dirtyTexts.set(id, input.value);
    const turn = turnById(id);
    if (turn) turn.text = input.value;
    autosave.markDirty();
    setStatus('Unsaved transcript edits', 'dirty');
    updateCanonical();
    onEdited();
  });

  root?.addEventListener('focusout', () => {
    window.setTimeout(() => {
      if (!root.contains(document.activeElement)) {
        editingTurnId = null;
        if (isDirty()) autosave.schedule({ immediate: true });
        renderTurns();
      }
    }, 0);
  });

  root?.parentElement?.addEventListener('click', async (event) => {
    const target = event.target instanceof Element ? event.target.closest('[data-turn-delete], [data-turn-add], [data-turn-retry]') : null;
    const display = event.target instanceof Element ? event.target.closest('[data-turn-display]') : null;
    if (!target && display) {
      if (actionPending) return;
      editingTurnId = display.dataset.turnDisplay;
      renderTurns();
      const input = root.querySelector(`[data-turn-text="${CSS.escape(editingTurnId)}"]`);
      input?.focus();
      return;
    }
    if (!target) return;
    if (actionPending) return;
    actionPending = true;
    target.disabled = true;
    try {
      if (target.hasAttribute('data-turn-delete')) {
        await persistAction((id) => `/api/v1/transcripts/${encodeURIComponent(id)}/turns/${encodeURIComponent(target.dataset.turnDelete)}`, 'DELETE', {});
      } else if (target.hasAttribute('data-turn-add')) {
        const existingIds = new Set(turns.map((turn) => turn.id));
        const added = await persistAction((id) => `/api/v1/transcripts/${encodeURIComponent(id)}/turns`, 'POST', {
          text: '', after_turn_id: turns.at(-1)?.id || null,
        });
        if (added) {
          const createdTurn = turns.find((turn) => !existingIds.has(turn.id));
          editingTurnId = createdTurn?.id || added.lastTurnId;
          renderTurns();
          const input = editingTurnId ? root.querySelector(`[data-turn-text="${CSS.escape(editingTurnId)}"]`) : null;
          input?.focus();
          input?.setSelectionRange(input.value.length, input.value.length);
        }
      } else {
        failed = false;
        setStatus('Retrying transcript save…', 'saving');
        await autosave.flush();
      }
    } catch (_) {
      failed = true;
      setStatus(target.hasAttribute('data-turn-add')
        ? 'Could not confirm the new turn. Reload the transcript before adding another.'
        : 'Could not update this transcript. Please try again.', 'action-error');
    } finally {
      actionPending = false;
      if (target.isConnected) target.disabled = false;
    }
  });

  const jumpToOffset = (start, end) => {
    if (masked) return false;
    const target = turns.find((turn) => Number.isInteger(turn.start_index) && Number.isInteger(turn.end_index)
      && start >= turn.start_index && end <= turn.end_index);
    if (!target) return false;
    editingTurnId = target.id;
    renderTurns();
    const input = root?.querySelector(`[data-turn-text="${CSS.escape(target.id)}"]`);
    if (!(input instanceof HTMLTextAreaElement)) return false;
    const startLocal = codepointToUtf16(target.text || '', start - target.start_index);
    const endLocal = codepointToUtf16(target.text || '', end - target.start_index);
    input.focus();
    input.setSelectionRange(startLocal, endLocal);
    input.scrollIntoView({ block: 'center' });
    return true;
  };

  return {
    render({ transcript_id = null, revision_id, turns: nextTurns, editable: canEdit, masked: isMasked = false, entities = [], pii_revision_id = revision_id } = {}) {
      const safetyStateChanged = editable !== Boolean(canEdit) || masked !== Boolean(isMasked);
      if (transcript_id && activeTranscriptId && transcript_id !== activeTranscriptId) {
        if (isDirty()) {
          remoteConflict = true;
          setStatus('Your previous transcript still has unsaved edits. Save or reload it before switching transcripts.', 'conflict');
          return;
        }
        activeTranscriptId = transcript_id;
        turns = [];
        revisionId = null;
        remoteConflict = false;
        editingTurnId = null;
        lastConfirmedSource = null;
        supersededRevisions.clear();
      } else if (transcript_id) {
        activeTranscriptId = transcript_id;
      }
      const nextRevision = revision_id || null;
      if (nextRevision !== revisionId && supersededRevisions.has(nextRevision)) {
        // A poll started before our accepted save may finish afterwards.
        // Its source must never replace our saved revision or dirty baseline.
        masked = Boolean(isMasked);
        editable = editable && Boolean(canEdit);
        if (lastConfirmedSource) onSourceSaved(lastConfirmedSource);
        updateCanonical();
        if (safetyStateChanged || !root.contains(document.activeElement)) renderTurns();
        return;
      }
      if (isDirty() && revisionId && nextRevision !== revisionId) {
        remoteConflict = true;
        setStatus('This transcript changed elsewhere. Your edits are kept here; reload before retrying.', 'conflict');
      } else if (!isDirty()) {
        revisionId = nextRevision;
      }
      editable = Boolean(canEdit);
      masked = Boolean(isMasked);
      piiRevisionId = pii_revision_id || null;
      piiEntities = isDirty() ? [] : (Array.isArray(entities) ? entities : []);
      if (!isDirty() && !remoteConflict) mergeServerTurns(nextTurns);
      else if (!remoteConflict) mergeServerTurns(nextTurns);
      if (safetyStateChanged || !root.contains(document.activeElement)) renderTurns();
    },
    getCanonicalText: canonicalText,
    jumpToOffset,
    flush: autosave.flush,
    get dirty() { return isDirty(); },
    get failed() { return failed; },
  };
}
