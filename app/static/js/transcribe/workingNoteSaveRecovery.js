/** In-memory identity and bounded replay for Working note mutations. */
export function createWorkingNoteAttemptStore() {
  let pending = null;

  const prepare = ({ endpoint, method = 'PATCH', payload, expectedRevision, editVersion = null, operationId, targetId = null, transcriptId = null }) => {
    if (pending) {
      if (pending.endpoint !== endpoint || pending.method !== method
        || (targetId && pending.targetId && pending.targetId !== targetId)
        || (transcriptId && pending.transcriptId && pending.transcriptId !== transcriptId)) {
        throw new Error('Resolve the pending Working note operation before switching targets.');
      }
      return pending;
    }
    if (!Number.isInteger(expectedRevision) || expectedRevision < 0) {
      throw new TypeError('Working note revision must be a non-negative integer.');
    }
    if (!operationId) throw new TypeError('Working note operation ID is required.');
    const frozenPayload = Object.freeze({
      ...payload,
      expected_revision: expectedRevision,
      operation_id: operationId,
    });
    pending = Object.freeze({
      endpoint,
      method,
      expectedRevision,
      editVersion,
      operationId,
      targetId,
      transcriptId,
      payload: frozenPayload,
      body: JSON.stringify(frozenPayload),
    });
    return pending;
  };

  const transmit = async (attempt, request) => {
    for (let tryNo = 0; tryNo < 2; tryNo += 1) {
      try {
        const response = await request(attempt);
        if (response.status >= 500) {
          if (tryNo === 0) continue;
          return { kind: 'uncertain', attempt };
        }
        if (!response.ok) return { kind: 'rejected', attempt, response };
        let result;
        if (attempt.method === 'DELETE') {
          result = { revision: Number(response.headers?.get?.('X-Working-Note-Revision')) };
        } else {
          result = await response.json();
        }
        const revision = Number(result?.revision);
        if (!Number.isInteger(revision) || revision !== attempt.expectedRevision + 1) {
          if (tryNo === 0) continue;
          return { kind: 'uncertain', attempt };
        }
        return { kind: 'acknowledged', attempt, result, response };
      } catch (error) {
        if (tryNo === 0) continue;
        return { kind: 'uncertain', attempt, error };
      }
    }
    return { kind: 'uncertain', attempt };
  };

  const acknowledge = (attempt) => {
    if (pending === attempt) pending = null;
  };

  const reject = (attempt) => {
    if (pending === attempt) pending = null;
  };

  const discard = () => { pending = null; };

  return {
    prepare,
    transmit,
    acknowledge,
    reject,
    discard,
    get pending() { return pending; },
  };
}
