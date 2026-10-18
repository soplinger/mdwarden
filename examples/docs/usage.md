# Usage

Run `mdwarden check` on a folder of pages.

## Options

- `--ignore GLOB` skips matching files.
- `--fail-on` picks which findings fail the build.

## Output

Each finding prints as `file:line: severity: [code] message`.

```text
this line [is not a link](nowhere.md) because it sits in a code block
```
