const DEFAULT_BOOTSTRAP = {
  activeTranscriptId: null,
  activeIngestionMode: null,
  nextLiveChunkSequenceNo: 1,
  hasSttSelection: false,
  hasLlmSelection: false,
  sttAvailable: false,
  sttStatusMessage: null,
  latestIngestionJobStatus: null,
  latestIngestionErrorMessage: null,
  showRedactionDebug: false,
  initialTranscriptErrorMessage: null,
  emisSections: [],
  activeTab: "output",
  viewerRole: "user",
};

export function readTranscribeBootstrap() {
  const bootstrapNode = document.getElementById("transcribe-bootstrap");
  if (!(bootstrapNode instanceof HTMLScriptElement) || !bootstrapNode.textContent) {
    return { ...DEFAULT_BOOTSTRAP };
  }
  try {
    const parsed = JSON.parse(bootstrapNode.textContent);
    return {
      ...DEFAULT_BOOTSTRAP,
      ...parsed,
      activeTab: parsed?.activeTab === "followups" ? "followups" : "output",
      nextLiveChunkSequenceNo: Number.isFinite(parsed?.nextLiveChunkSequenceNo)
        ? parsed.nextLiveChunkSequenceNo
        : DEFAULT_BOOTSTRAP.nextLiveChunkSequenceNo,
      viewerRole: parsed?.viewerRole === "leader" ? "leader" : "user",
      emisSections: Array.isArray(parsed?.emisSections) ? parsed.emisSections : [],
    };
  } catch (error) {
    console.error("Could not parse transcribe bootstrap payload.", error);
    return { ...DEFAULT_BOOTSTRAP };
  }
}
