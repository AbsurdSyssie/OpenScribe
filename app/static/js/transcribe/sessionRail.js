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

function compareSessionRailItems(left, right) {
  const leftTime = Date.parse(left?.created_at || '');
  const rightTime = Date.parse(right?.created_at || '');
  const safeLeftTime = Number.isFinite(leftTime) ? leftTime : 0;
  const safeRightTime = Number.isFinite(rightTime) ? rightTime : 0;
  if (safeRightTime !== safeLeftTime) return safeRightTime - safeLeftTime;
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
  incoming.forEach((item) => {
    const id = String(item.id);
    mergedById.set(id, { ...(mergedById.get(id) || {}), ...item });
  });

  const topPageBoundary = sortSessionRailItems(topPage).at(-1);
  const retained = preserveLoaded && topPageBoundary
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
