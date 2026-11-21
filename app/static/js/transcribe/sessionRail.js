export function formatSessionRailCreatedAt(value) {
  if (!value) return '';
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return value;
  return new Intl.DateTimeFormat('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(new Date(timestamp));
}

export function syncSessionRailCreatedAt(node, value) {
  const createdAt = node?.querySelector?.('[data-session-created-at]');
  if (!createdAt) return;
  createdAt.dataset.sessionCreatedAt = value || '';
  createdAt.textContent = formatSessionRailCreatedAt(value);
  createdAt.dataset.sessionCreatedAtReady = 'true';
  createdAt.removeAttribute?.('aria-hidden');
}

export function sessionRailGroup(value) {
  const timestamp = Date.parse(value || '');
  if (!Number.isFinite(timestamp)) {
    return { dateKey: 'unknown', dateLabel: 'Earlier', period: 'afternoon' };
  }
  const date = new Date(timestamp);
  const dateKey = [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0'),
  ].join('-');
  return {
    dateKey,
    dateLabel: new Intl.DateTimeFormat('en-GB', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
    }).format(date),
    period: date.getHours() < 12 ? 'morning' : 'afternoon',
  };
}

export function keepSessionRailItemVisible({ scrollContainer, item, inset = 12, behavior = 'smooth' }) {
  if (!scrollContainer || !item) return null;
  const itemRect = item.getBoundingClientRect();
  const scrollRect = scrollContainer.getBoundingClientRect();
  let delta = 0;
  if (itemRect.top < scrollRect.top) {
    delta = itemRect.top - scrollRect.top - inset;
  } else if (itemRect.bottom > scrollRect.bottom) {
    delta = itemRect.bottom - scrollRect.bottom + inset;
  } else {
    return scrollContainer.scrollTop;
  }
  const nextTop = Math.max(0, scrollContainer.scrollTop + delta);
  scrollContainer.scrollTo({ top: nextTop, behavior });
  return nextTop;
}

export function sessionRailTimestamp(item) {
  return item?.first_content_at || item?.created_at || '';
}

export function mergeSessionRailItem(previous, incoming) {
  return { ...previous, ...incoming, first_content_at: previous?.first_content_at || incoming.first_content_at };
}

function subMillisecondMicroseconds(timestamp) {
  const digits = /\.\d{3}(\d{1,3})(?:Z|[+-]\d{2}:?\d{2})?$/i.exec(timestamp)?.[1];
  return Number(digits?.padEnd(3, '0') || 0);
}

function compareSessionRailItems(left, right) {
  const leftTimestamp = sessionRailTimestamp(left);
  const rightTimestamp = sessionRailTimestamp(right);
  const leftTime = Date.parse(leftTimestamp);
  const rightTime = Date.parse(rightTimestamp);
  const safeLeftTime = Number.isFinite(leftTime) ? leftTime : 0;
  const safeRightTime = Number.isFinite(rightTime) ? rightTime : 0;
  if (safeRightTime !== safeLeftTime) return safeRightTime - safeLeftTime;
  if (leftTime === rightTime) {
    // PostgreSQL history cursors use microsecond precision.
    const microseconds = subMillisecondMicroseconds(rightTimestamp) - subMillisecondMicroseconds(leftTimestamp);
    if (microseconds !== 0) return microseconds;
  }
  return String(right?.id || '').localeCompare(String(left?.id || ''));
}

export function sortSessionRailItems(items) {
  return [...items].sort(compareSessionRailItems);
}

export function reconcileSessionRailItems({
  currentItems,
  workspaceItems,
  pageSize,
  preserveLoaded,
}) {
  const current = Array.isArray(currentItems) ? currentItems.filter((item) => item?.id) : [];
  const incoming = Array.isArray(workspaceItems) ? workspaceItems.filter((item) => item?.id) : [];
  const safePageSize = Number.isInteger(pageSize) && pageSize > 0 ? pageSize : incoming.length;
  if (current.length === 0) return sortSessionRailItems(incoming);

  const topPage = incoming.slice(0, safePageSize);
  const supplemental = incoming.slice(safePageSize);
  const topPageIds = new Set(topPage.map((item) => String(item.id)));
  const mergedById = new Map(current.map((item) => [String(item.id), item]));
  const firstContentMovedIntoTop = topPage.some((item) => (
    item.first_content_at && !mergedById.get(String(item.id))?.first_content_at
  ));
  incoming.forEach((item) => {
    const id = String(item.id);
    mergedById.set(id, mergeSessionRailItem(mergedById.get(id), item));
  });

  const topPageBoundary = sortSessionRailItems(topPage.map((item) => mergedById.get(String(item.id)))).at(-1);
  const retained = (preserveLoaded || firstContentMovedIntoTop) && topPageBoundary
    ? current.filter((item) => (
      !topPageIds.has(String(item.id))
      && compareSessionRailItems(item, topPageBoundary) > 0
    ))
    : [];
  const nextItems = [
    ...topPage.map((item) => mergedById.get(String(item.id))),
    ...retained.map((item) => mergedById.get(String(item.id))),
  ].filter(Boolean);
  supplemental.forEach((item) => {
    const merged = mergedById.get(String(item.id));
    if (merged && !nextItems.some((candidate) => candidate.id === merged.id)) nextItems.push(merged);
  });
  return sortSessionRailItems(nextItems);
}
