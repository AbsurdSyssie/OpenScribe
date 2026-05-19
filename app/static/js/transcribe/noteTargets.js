export const workingNoteTargetId = (transcriptId = '') => `working:${transcriptId || ''}`;

export const isWorkingNoteTargetId = (targetId = '') => String(targetId || '').startsWith('working:');

export const generatedNoteTargetId = (documentId = '') => documentId || '';
