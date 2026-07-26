#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]

OPERATIONAL_DOCS: tuple[Path, ...] = tuple(
    ROOT / path
    for path in (
        "README.md",
        "docs/README.md",
        "docs/setup.md",
        "docs/docker.md",
        "docs/environment.md",
        "docs/auth.md",
        "docs/security.md",
        "docs/security-xss.md",
        "docs/mfa-secret-encryption.md",
        "docs/api.md",
        "docs/workspace.md",
        "docs/transcript-capture.md",
        "docs/live_stt.md",
        "docs/stt-config.md",
        "docs/llm-providers.md",
        "docs/gemini-enterprise-setup.md",
        "docs/testing.md",
        "docs/dbtesting.md",
        "docs/tutorials/README.md",
        "docs/tutorials/onboarding.md",
        "docs/tutorials/user.md",
        "docs/tutorials/team-leader.md",
        "docs/tutorials/admin.md",
        "docs/tutorials/system-admin-setup.md",
    )
)

INLINE_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
LOCAL_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<![\w.-])/home/[^\s)`]+"),
    re.compile(r"(?<![\w.-])/Users/[^\s)`]+"),
    re.compile(r"\b[A-Za-z]:\\Users\\[^\s)`]+"),
)


def link_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")]
    elif " " in target:
        # Markdown permits an optional quoted title after the target.
        target = target.split(" ", 1)[0]
    return unquote(target)


def is_external_or_anchor(target: str) -> bool:
    lowered = target.lower()
    return (
        not target
        or target.startswith("#")
        or lowered.startswith(("http://", "https://", "mailto:", "data:"))
    )


def resolved_local_target(document: Path, target: str) -> Path | None:
    clean = target.split("#", 1)[0].split("?", 1)[0]
    if not clean:
        return None
    if clean.startswith("/"):
        # Application/browser route examples are not repository-file links.
        return None
    return (document.parent / clean).resolve()


def main() -> int:
    failures: list[str] = []

    for document in OPERATIONAL_DOCS:
        relative_document = document.relative_to(ROOT)
        if not document.is_file():
            failures.append(f"missing maintained document: {relative_document}")
            continue

        text = document.read_text(encoding="utf-8")
        for pattern in LOCAL_PATH_PATTERNS:
            for match in pattern.finditer(text):
                failures.append(
                    f"{relative_document}: machine-specific local path: {match.group(0)}"
                )

        for raw_target in INLINE_LINK_RE.findall(text):
            target = link_target(raw_target)
            if is_external_or_anchor(target):
                continue
            resolved = resolved_local_target(document, target)
            if resolved is None:
                continue
            try:
                resolved.relative_to(ROOT)
            except ValueError:
                failures.append(
                    f"{relative_document}: link escapes repository: {target}"
                )
                continue
            if not resolved.exists():
                failures.append(
                    f"{relative_document}: missing link target: {target}"
                )

    if failures:
        print("Operational documentation check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(f"Checked {len(OPERATIONAL_DOCS)} maintained documentation files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
