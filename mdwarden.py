#!/usr/bin/env python3
"""mdwarden: an offline Markdown project checker.

Scans a folder of Markdown pages for broken relative links, missing or
ambiguous #anchors, malformed URLs, heading-level jumps and orphaned pages.
Findings are printed as file:line messages and the exit code is set for CI.
Nothing is fetched from the network.
"""

import argparse
import fnmatch
import json
import os
import posixpath
import re
import sys
from dataclasses import asdict, dataclass
from urllib.parse import unquote, urlsplit

MD_EXTS = (".md", ".markdown")
DEFAULT_ENTRIES = ("readme.md", "index.md")
TOC_START = "<!-- toc:start -->"
TOC_END = "<!-- toc:end -->"

ERROR = "error"
WARNING = "warning"

HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
CODE_SPAN_RE = re.compile(r"(`+)(.+?)\1")
LINK_RE = re.compile(
    r"(!?)\[([^\]]*)\]\(\s*(<[^>]*>|[^)\s]*)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)"
)
LINK_MARKUP_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")


@dataclass
class Heading:
    line: int
    level: int
    text: str
    anchor: str


@dataclass
class Link:
    line: int
    kind: str
    text: str
    target: str


@dataclass
class Finding:
    file: str
    line: int
    severity: str
    code: str
    message: str


def slugify(text):
    """Return the GitHub-style anchor for a heading's text."""
    text = LINK_MARKUP_RE.sub(lambda m: m.group(1), text)
    text = text.replace("`", "").replace("*", "").strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def strip_code_spans(line):
    """Blank out inline code so links inside backticks are ignored."""
    return CODE_SPAN_RE.sub(lambda m: " " * len(m.group(0)), line)


def iter_prose(text):
    """Yield (line number, line) for every line outside fenced code blocks."""
    fence = None
    for number, line in enumerate(text.splitlines(), start=1):
        match = FENCE_RE.match(line)
        if fence is None:
            if match:
                fence = match.group(1)
                continue
            yield number, line
        elif (
            match
            and match.group(1)[0] == fence[0]
            and len(match.group(1)) >= len(fence)
            and not match.group(2).strip()
        ):
            fence = None


def parse_markdown(text):
    """Return (headings, links) for one Markdown document."""
    headings, links = [], []
    seen = {}
    for number, line in iter_prose(text):
        heading = HEADING_RE.match(line)
        if heading:
            title = (heading.group(2) or "").strip()
            base = slugify(title)
            count = seen.get(base, 0)
            seen[base] = count + 1
            anchor = base if count == 0 else f"{base}-{count}"
            headings.append(Heading(number, len(heading.group(1)), title, anchor))
        for match in LINK_RE.finditer(strip_code_spans(line)):
            kind = "image" if match.group(1) else "link"
            target = match.group(3).strip("<>")
            links.append(Link(number, kind, match.group(2), target))
    return headings, links


def read_text(path):
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as handle:
        return handle.read()


def collect_files(root, ignore=()):
    """Return sorted relative paths of Markdown files, skipping hidden folders."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if not name.lower().endswith(MD_EXTS):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            rel = rel.replace(os.sep, "/")
            if any(fnmatch.fnmatch(rel, pattern) for pattern in ignore):
                continue
            found.append(rel)
    return sorted(found)


def normalize_rel(path):
    return posixpath.normpath(path.replace(os.sep, "/"))


def heading_findings(rel, headings):
    findings = []
    previous = None
    for heading in headings:
        if not heading.text:
            findings.append(Finding(rel, heading.line, WARNING, "empty-heading",
                                    "heading has no text"))
        elif heading.anchor != slugify(heading.text):
            findings.append(Finding(
                rel, heading.line, WARNING, "duplicate-heading",
                f"heading repeats an earlier one; its anchor is #{heading.anchor}"))
        if previous is not None and heading.level > previous + 1:
            findings.append(Finding(
                rel, heading.line, WARNING, "heading-skip",
                f"heading jumps from h{previous} to h{heading.level}"))
        previous = heading.level
    return findings


def check_link(rel, link, root, file_set, anchors_of):
    """Return (problems, page reached) for one link found in page `rel`."""
    problems = []

    def problem(severity, code, message):
        problems.append(Finding(rel, link.line, severity, code, message))

    target = link.target
    if not target:
        problem(ERROR, "empty-link", "link target is empty")
        return problems, None

    scheme = SCHEME_RE.match(target)
    if scheme:
        if scheme.group(1).lower() in ("http", "https") and not urlsplit(target).netloc:
            problem(ERROR, "bad-url", f"URL has no host: {target}")
        return problems, None

    path, _, fragment = target.partition("#")
    if not path:
        if fragment and unquote(fragment) not in anchors_of(rel):
            problem(ERROR, "broken-anchor", f"no heading '#{fragment}' in this page")
        return problems, None

    dest = posixpath.normpath(posixpath.join(posixpath.dirname(rel), unquote(path)))
    if dest == ".." or dest.startswith("../") or posixpath.isabs(dest):
        problem(WARNING, "outside-root", f"link leaves the project: {path}")
        return problems, None

    absolute = os.path.join(root, dest)
    if dest not in file_set and not os.path.exists(absolute):
        problem(ERROR, "broken-link", f"target not found: {path}")
        return problems, None

    if fragment and dest.lower().endswith(MD_EXTS) and os.path.isfile(absolute):
        if unquote(fragment) not in anchors_of(dest):
            problem(ERROR, "broken-anchor", f"no heading '#{fragment}' in {dest}")

    reached = dest if dest in file_set and dest != rel else None
    return problems, reached


def run_check(root, entries=(), ignore=()):
    """Check every Markdown page under `root`. Returns (files, findings)."""
    root = os.path.abspath(root)
    files = collect_files(root, ignore)
    file_set = set(files)
    cache = {}

    def doc(rel):
        if rel not in cache:
            cache[rel] = parse_markdown(read_text(os.path.join(root, rel)))
        return cache[rel]

    def anchors_of(rel):
        return {heading.anchor for heading in doc(rel)[0]}

    findings, reached = [], set()
    for rel in files:
        headings, links = doc(rel)
        findings.extend(heading_findings(rel, headings))
        for link in links:
            problems, target = check_link(rel, link, root, file_set, anchors_of)
            findings.extend(problems)
            if target:
                reached.add(target)

    exempt = set(entries) if entries else {f for f in files if f.lower() in DEFAULT_ENTRIES}
    for rel in files:
        if rel not in reached and rel not in exempt:
            findings.append(Finding(rel, 1, WARNING, "orphan",
                                    "no other page links to this file"))

    findings.sort(key=lambda f: (f.file, f.line, f.code))
    return files, findings


def should_fail(findings, fail_on):
    if fail_on == "never":
        return False
    if fail_on == "warning":
        return bool(findings)
    return any(f.severity == ERROR for f in findings)


def build_toc(headings, max_level=3):
    """Return nested Markdown bullet lines linking to each heading."""
    shown = [h for h in headings if h.level <= max_level and h.text]
    if not shown:
        return []
    base = min(h.level for h in shown)
    lines = []
    for heading in shown:
        label = heading.text.replace("[", "\\[").replace("]", "\\]")
        indent = "  " * (heading.level - base)
        lines.append(f"{indent}- [{label}](#{heading.anchor})")
    return lines


def replace_toc(text, toc_lines):
    """Swap the lines between the toc markers for `toc_lines`."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.strip() == TOC_START]
    ends = [i for i, line in enumerate(lines) if line.strip() == TOC_END]
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise ValueError(f"expected one '{TOC_START}' line followed by '{TOC_END}'")
    body = [line + newline for line in toc_lines]
    return "".join(lines[: starts[0] + 1] + body + lines[ends[0]:])


def cmd_check(args):
    if not os.path.isdir(args.root):
        print(f"mdwarden: not a directory: {args.root}", file=sys.stderr)
        return 2
    entries = [normalize_rel(e) for e in args.entry]
    files, findings = run_check(args.root, entries, args.ignore)
    errors = sum(1 for f in findings if f.severity == ERROR)
    warnings = len(findings) - errors
    if args.format == "json":
        report = {
            "files": len(files),
            "errors": errors,
            "warnings": warnings,
            "findings": [asdict(f) for f in findings],
        }
        print(json.dumps(report, indent=2))
    else:
        for f in findings:
            print(f"{f.file}:{f.line}: {f.severity}: [{f.code}] {f.message}")
        print(f"mdwarden: {len(files)} files, {errors} errors, {warnings} warnings")
    return 1 if should_fail(findings, args.fail_on) else 0


def cmd_toc(args):
    if not os.path.isfile(args.file):
        print(f"mdwarden: no such file: {args.file}", file=sys.stderr)
        return 2
    text = read_text(args.file)
    headings, _ = parse_markdown(text)
    toc_lines = build_toc(headings, args.max_level)
    if not args.write:
        print("\n".join(toc_lines))
        return 0
    try:
        updated = replace_toc(text, toc_lines)
    except ValueError as error:
        print(f"mdwarden: {args.file}: {error}", file=sys.stderr)
        return 2
    with open(args.file, "w", encoding="utf-8", newline="") as handle:
        handle.write(updated)
    print(f"mdwarden: updated table of contents in {args.file} ({len(toc_lines)} entries)")
    return 0


def cmd_links(args):
    if not os.path.isfile(args.file):
        print(f"mdwarden: no such file: {args.file}", file=sys.stderr)
        return 2
    _, links = parse_markdown(read_text(args.file))
    for link in links:
        print(f"{args.file}:{link.line}: {link.kind}: {link.target or '(empty)'}")
    print(f"mdwarden: {len(links)} links")
    return 0


def cmd_slug(args):
    print(slugify(" ".join(args.text)))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mdwarden",
        description="Offline Markdown project checker: links, anchors, headings, orphans.")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="check every page under a folder")
    check.add_argument("root", nargs="?", default=".", help="folder to scan (default: .)")
    check.add_argument("--entry", action="append", default=[], metavar="FILE",
                       help="page allowed to have no inbound links "
                            "(default: README.md and index.md at the root)")
    check.add_argument("--ignore", action="append", default=[], metavar="GLOB",
                       help="skip relative paths matching this glob (repeatable)")
    check.add_argument("--format", choices=("text", "json"), default="text")
    check.add_argument("--fail-on", choices=("error", "warning", "never"), default="error",
                       help="which findings make the exit code 1 (default: error)")
    check.set_defaults(func=cmd_check)

    toc = commands.add_parser("toc", help="print or update a table of contents")
    toc.add_argument("file")
    toc.add_argument("--max-level", type=int, default=3, help="deepest heading level (default: 3)")
    toc.add_argument("--write", action="store_true",
                     help=f"replace the lines between {TOC_START} and {TOC_END}")
    toc.set_defaults(func=cmd_toc)

    links = commands.add_parser("links", help="list every link in one page")
    links.add_argument("file")
    links.set_defaults(func=cmd_links)

    slug = commands.add_parser("slug", help="show the anchor GitHub would give a heading")
    slug.add_argument("text", nargs="+")
    slug.set_defaults(func=cmd_slug)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
