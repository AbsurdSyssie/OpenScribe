export function createExampleConsultationInitializer({
  pending,
  hasSelectedTranscript,
  createExample,
  canOpenTranscript = () => true,
  openTranscript,
  refreshWorkspace,
  onError,
}) {
  let started = false;
  return async () => {
    if (!pending || hasSelectedTranscript() || started) return false;
    started = true;
    try {
      const transcriptId = await createExample();
      if (transcriptId && canOpenTranscript()) await openTranscript(transcriptId);
      else await refreshWorkspace();
      return true;
    } catch (error) {
      onError?.(error);
      return false;
    }
  };
}

export function createExampleConsultationBoundary({ isExample, createConsultation }) {
  let inFlight = null;
  return async () => {
    if (!isExample()) return true;
    if (!inFlight) {
      inFlight = Promise.resolve(createConsultation())
        .then((created) => Boolean(created))
        .finally(() => { inFlight = null; });
    }
    return inFlight;
  };
}
