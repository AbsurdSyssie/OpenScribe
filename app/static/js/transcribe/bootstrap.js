const DEFAULT_BOOTSTRAP = {
  activeTranscriptId: null,
  activeIngestionMode: null,
  nextLiveChunkSequenceNo: 1,
  hasSttSelection: false,
  hasDictationSttSelection: false,
  hasLlmSelection: false,
  sttAvailable: false,
  sttHealth: null,
  dictationSttAvailable: false,
  sttStatusMessage: null,
  dictationSttStatusMessage: null,
  userAppPreferences: {},
  latestIngestionJobStatus: null,
  latestIngestionErrorMessage: null,
  showRedactionDebug: false,
  initialTranscriptErrorMessage: null,
  emisSections: [],
  activeTranscriptPiiEntities: [],
  activeTranscriptRedactionStatus: { status: "not_run", entity_count: 0, error_code: null },
  activeTab: "output",
  viewerRole: "user",
  smartPhrases: [],
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
      activeTranscriptPiiEntities: Array.isArray(parsed?.activeTranscriptPiiEntities) ? parsed.activeTranscriptPiiEntities : [],
      activeTranscriptRedactionStatus: parsed?.activeTranscriptRedactionStatus || DEFAULT_BOOTSTRAP.activeTranscriptRedactionStatus,
      smartPhrases: Array.isArray(parsed?.smartPhrases) ? parsed.smartPhrases : [],
    };
  } catch (error) {
    console.error("Could not parse transcribe bootstrap payload.", error);
    return { ...DEFAULT_BOOTSTRAP };
  }
}
