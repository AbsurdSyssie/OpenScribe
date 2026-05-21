export function captureNoteDirtyBaseline({
  currentUpdatedAt = '',
  workingNoteUpdatedAt = '',
  isWorkingNote = false,
} = {}) {
  return currentUpdatedAt || (isWorkingNote ? workingNoteUpdatedAt || '' : '');
}

export function noteBaselineForSave({
  targetId = '',
  noteEditorDirty = false,
  dirtyNoteTargetId = null,
  dirtyNoteExpectedUpdatedAt = null,
  currentUpdatedAt = '',
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
  return currentUpdatedAt || (isWorkingNoteTarget(targetId) ? workingNoteUpdatedAt || null : '');
}
