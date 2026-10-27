(() => {
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

  const valueFor = (row, index) => row.cells[index]?.dataset.sortValue ?? '';
  const isEmpty = (value) => {
    const normalized = value == null ? '' : String(value).trim();
    return normalized === '' || normalized === '—' || normalized === '–';
  };

  const compareValues = (left, right, type, direction, leftCell, rightCell) => {
    const leftEmpty = isEmpty(left);
    const rightEmpty = isEmpty(right);
    if (leftEmpty || rightEmpty) return leftEmpty === rightEmpty ? 0 : (leftEmpty ? 1 : -1);

    let comparison = 0;
    if (type === 'number') {
      const leftNumber = Number(left);
      const rightNumber = Number(right);
      comparison = leftNumber - rightNumber;
    } else if (type === 'date') {
      comparison = Date.parse(left) - Date.parse(right);
    } else if (type === 'resource-number') {
      const groupComparison = collator.compare(leftCell?.dataset.sortGroup ?? '', rightCell?.dataset.sortGroup ?? '');
      if (groupComparison !== 0) return groupComparison;
      comparison = Number(left) - Number(right);
    } else {
      comparison = collator.compare(String(left), String(right));
    }
    return Number.isFinite(comparison) ? comparison * direction : 0;
  };

  const sortableRows = (table) => [...table.tBodies].flatMap((body) => [...body.rows]).filter((row) => {
    const cells = [...row.cells];
    return cells.some((cell) => cell.tagName === 'TD') && !cells.some((cell) => cell.colSpan > 1);
  });

  document.querySelectorAll('table[data-sortable-table]').forEach((table) => {
    const rows = sortableRows(table);
    if (rows.length < 2) return;

    const headers = [...table.tHead?.rows ?? []].flatMap((row) => [...row.cells]);
    headers.forEach((header, index) => {
      const type = header.dataset.sortType;
      if (!type) return;

      const label = header.textContent.trim().replace(/\s+/g, ' ');
      const hint = header.title ? ` ${header.title}.` : '';
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'usage-table-sort-button';
      button.textContent = label;
      const defaultLabel = `Sort by ${label}.${hint} Activate to sort ascending.`;
      button.setAttribute('aria-label', defaultLabel);
      header.replaceChildren(button);

      button.addEventListener('click', () => {
        const active = header.getAttribute('aria-sort');
        const ascending = active !== 'ascending';
        const direction = ascending ? 1 : -1;
        headers.forEach((otherHeader) => {
          if (otherHeader !== header) {
            otherHeader.removeAttribute('aria-sort');
            otherHeader.querySelector('.usage-table-sort-button')?.setAttribute(
              'aria-label',
              `Sort by ${otherHeader.textContent.trim().replace(/\s+/g, ' ')}.${otherHeader.title ? ` ${otherHeader.title}.` : ''} Activate to sort ascending.`,
            );
          }
        });
        header.setAttribute('aria-sort', ascending ? 'ascending' : 'descending');
        button.setAttribute(
          'aria-label',
          `Sorted by ${label}, ${ascending ? 'ascending' : 'descending'}.${hint} Activate to sort ${ascending ? 'descending' : 'ascending'}.`,
        );

        rows.sort((leftRow, rightRow) => {
          const leftCell = leftRow.cells[index];
          const rightCell = rightRow.cells[index];
          let comparison = compareValues(
            valueFor(leftRow, index), valueFor(rightRow, index), type, direction, leftCell, rightCell,
          );
          if (comparison === 0 && type === 'text') {
            comparison = compareValues(
              leftCell?.dataset.sortSecondary ?? '', rightCell?.dataset.sortSecondary ?? '',
              'text', direction, leftCell, rightCell,
            );
          }
          return comparison;
        });
        const bodies = [...table.tBodies];
        rows.forEach((row) => bodies.find((body) => body.contains(row))?.append(row));
      });
    });
  });
})();
