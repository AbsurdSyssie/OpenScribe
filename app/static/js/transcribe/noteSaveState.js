export function captureNoteDirtyBaseline({
  currentUpdatedAt = '',
  currentUpdatedAtAvailable = false,
  workingNoteUpdatedAt = '',
  isWorkingNote = false,
} = {}) {
  if (currentUpdatedAtAvailable) return currentUpdatedAt || '';
  return currentUpdatedAt || (isWorkingNote ? workingNoteUpdatedAt || '' : '');
}

export function noteBaselineForSave({
  targetId = '',
  noteEditorDirty = false,
  dirtyNoteTargetId = null,
  dirtyNoteExpectedUpdatedAt = null,
  currentUpdatedAt = '',
  currentUpdatedAtAvailable = false,
  workingNoteUpdatedAt = '',
  isWorkingNoteTarget = () => false,
} = {}) {
  if (
    noteEditorDirty
    && dirtyNoteTargetId === targetId
    && dirtyNoteExpectedUpdatedAt !== null
  ) {
    return dirtyNoteExpectedUpdatedAt || null;
  }
  if (currentUpdatedAtAvailable) return currentUpdatedAt || null;
  return currentUpdatedAt || (isWorkingNoteTarget(targetId) ? workingNoteUpdatedAt || null : '');
}
