# mdwarden

**Offline Markdown project checker: broken links, missing anchors, heading jumps and orphaned pages, with file:line errors and CI exit codes.**

Point mdwarden at a folder of Markdown and it tells you which links no longer work, which `#anchors` point at nothing, which pages nobody links to, and where your heading levels skip. It reads files only. It never touches the network, so a `https://` link is checked for shape, not reachability.

## Features

- Scans every `.md` and `.markdown` file under a folder, skipping hidden folders such as `.git`
- Finds broken relative links and images, including folder links and percent-encoded paths
- Checks `#anchors` on the same page and in other pages, using GitHub's heading-to-anchor rules
- Gives duplicate headings GitHub-style suffixes (`setup`, `setup-1`) so links to them resolve
- Flags malformed web URLs such as `https://` with no host, without fetching anything
- Flags empty link targets and empty headings
- Warns about heading jumps such as `h1` straight to `h4`
- Reports orphaned pages that no other page links to, with `--entry` to exempt pages
- Warns about links that climb out of the project folder
- Ignores links inside fenced code blocks and inline code spans, and handles CRLF files
- `--ignore GLOB` skips drafts or vendored folders
- `--format json` for dashboards and scripts
- `--fail-on error|warning|never` sets which findings fail the build (exit `1`); bad usage exits `2`
- `toc` builds a nested table of contents, and `toc --write` updates the region between `<!-- toc:start -->` and `<!-- toc:end -->` in place
- `links` lists every link in one page with its line number
- `slug` shows the anchor GitHub would generate for any heading text

## Quick start

Requires Python 3.10 or newer. There is nothing to install.

```sh
python3 mdwarden.py check examples
```

The bundled `examples/` folder has deliberate problems, so this reports findings and exits `1`. Each finding prints as `file:line: severity: [code] message`, with paths relative to the folder you scanned:

```text
README.md:7: error: [broken-link] target not found: CHANGELOG.md
README.md:8: error: [broken-anchor] no heading '#no-such-section' in this page
docs/install.md:5: warning: [heading-skip] heading jumps from h1 to h4
docs/install.md:8: error: [bad-url] URL has no host: https://
docs/notes.md:1: warning: [orphan] no other page links to this file
mdwarden: 4 files, 3 errors, 2 warnings
```

Fail the build only on errors, or on warnings too:

```sh
python3 mdwarden.py check examples --fail-on warning
python3 mdwarden.py check examples --format json --ignore "docs/notes.md"
```

Other commands:

```sh
python3 mdwarden.py links examples/README.md      # every link, with line numbers
python3 mdwarden.py toc examples/README.md        # print a table of contents
python3 mdwarden.py toc examples/README.md --write # update the marked region in place
python3 mdwarden.py slug "Getting Started: v2!"   # -> getting-started-v2
```

To keep a generated table of contents current, put these markers in your page where the list should go:

```markdown
<!-- toc:start -->
<!-- toc:end -->
```

## How to run

```sh
python3 mdwarden.py --help
python3 mdwarden.py check <folder> [--entry FILE]... [--ignore GLOB]... [--format text|json] [--fail-on error|warning|never]
python3 mdwarden.py toc <file> [--max-level N] [--write]
python3 mdwarden.py links <file>
python3 mdwarden.py slug <text>...
```

Exit codes: `0` when nothing fails the threshold, `1` when findings meet `--fail-on`, `2` for usage errors such as a missing folder or a missing TOC marker.

Known limits: reference-style links (`[x][id]`), nested parentheses inside a link target, and autolinks in `<angle brackets>` are not checked. Links are matched on one line.

## How to test

The tests use the standard library `unittest` module, so they run with no install:

```sh
python3 -m unittest discover -s tests -v
```

pytest also works if you have it: `python3 -m pytest tests`.

## Layout

```text
mdwarden.py          the whole tool: parser, checker, TOC writer and CLI
examples/            a small project with deliberate problems to try it on
tests/test_mdwarden.py  unit and CLI tests
```

## License

No license file is included. Add one before publishing.
