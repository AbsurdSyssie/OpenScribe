/** Shared debounce, serialized-save, queued-edit and flush behavior for workspace editors. */
export function createAutosaveController({
  save,
  isDirty,
  onSaved = () => {},
  onFailure = () => {},
  delay = 700,
  timers = window,
} = {}) {
  if (typeof save !== 'function' || typeof isDirty !== 'function') {
    throw new TypeError('Autosave requires save and isDirty callbacks.');
  }
  let editVersion = 0;
  let timer = null;
  let inFlight = null;
  let queued = false;
  let disposed = false;

  const schedule = ({ immediate = false } = {}) => {
    if (disposed || !isDirty()) return;
    if (timer !== null) timers.clearTimeout(timer);
    timer = timers.setTimeout(() => {
      timer = null;
      void persist();
    }, immediate ? 0 : delay);
  };

  const markDirty = ({ immediate = false } = {}) => {
    editVersion += 1;
    schedule({ immediate });
    return editVersion;
  };

  const persist = async ({ keepalive = false, ...context } = {}) => {
    if (disposed || !isDirty()) return null;
    if (inFlight) {
      queued = true;
      return inFlight;
    }
    const version = editVersion;
    inFlight = (async () => {
      try {
        const result = await Promise.resolve().then(() => save({ version, keepalive, ...context }));
        if (result == null || result === false) return null;
        if (!disposed) onSaved(result, { version, current: version === editVersion });
        return result;
      } catch (error) {
        if (!disposed) onFailure(error);
        return null;
      } finally {
        inFlight = null;
        if (queued) {
          queued = false;
          if (isDirty()) schedule({ immediate: true });
        }
      }
    })();
    return inFlight;
  };

  const flush = async ({ keepalive = false, ...context } = {}) => {
    let result = null;
    while (!disposed) {
      if (timer !== null) {
        timers.clearTimeout(timer);
        timer = null;
      }
      if (inFlight) {
        queued = true;
        result = await inFlight;
        if (result == null) return null;
        continue;
      }
      if (!isDirty()) return result;
      result = await persist({ keepalive, ...context });
      if (result == null) return null;
    }
    return null;
  };

  const dispose = () => {
    disposed = true;
    queued = false;
    if (timer !== null) timers.clearTimeout(timer);
    timer = null;
  };

  return {
    markDirty,
    schedule,
    persist,
    flush,
    dispose,
    get editVersion() { return editVersion; },
    get pending() { return timer !== null || Boolean(inFlight) || isDirty(); },
  };
}
