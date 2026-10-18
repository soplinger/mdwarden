import contextlib
import io
import json
import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mdwarden  # noqa: E402


def write_tree(root, files):
    for rel, text in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(textwrap.dedent(text).lstrip("\n"))


def codes(findings):
    return sorted((f.file, f.line, f.code) for f in findings)


class SlugTests(unittest.TestCase):
    def test_basic_punctuation_removed(self):
        self.assertEqual(mdwarden.slugify("Hello, World!"), "hello-world")

    def test_unicode_letters_kept(self):
        self.assertEqual(mdwarden.slugify("Naïve café"), "naïve-café")

    def test_underscores_kept_and_markup_stripped(self):
        self.assertEqual(mdwarden.slugify("`my_func` and [link](x.md)"), "my_func-and-link")

    def test_asterisks_removed(self):
        self.assertEqual(mdwarden.slugify("**Bold** start"), "bold-start")


class ParseTests(unittest.TestCase):
    def test_closing_hashes_are_not_part_of_title(self):
        headings, _ = mdwarden.parse_markdown("## Title ##\n")
        self.assertEqual(headings[0].text, "Title")
        self.assertEqual(headings[0].level, 2)

    def test_hash_inside_word_is_kept(self):
        headings, _ = mdwarden.parse_markdown("# C#\n")
        self.assertEqual(headings[0].text, "C#")

    def test_hashtag_without_space_is_not_a_heading(self):
        headings, _ = mdwarden.parse_markdown("#5 things\n")
        self.assertEqual(headings, [])

    def test_duplicate_headings_get_numbered_anchors(self):
        headings, _ = mdwarden.parse_markdown("# Setup\n\n# Setup\n\n# Setup\n")
        self.assertEqual([h.anchor for h in headings], ["setup", "setup-1", "setup-2"])

    def test_fenced_code_is_ignored(self):
        text = "# Real\n\n```\n# Fake\n[x](missing.md)\n```\n"
        headings, links = mdwarden.parse_markdown(text)
        self.assertEqual([h.text for h in headings], ["Real"])
        self.assertEqual(links, [])

    def test_tilde_fence_and_short_closer(self):
        text = "~~~~\n```\n# Still code\n~~~\n# Still code too\n~~~~\n# Out\n"
        headings, _ = mdwarden.parse_markdown(text)
        self.assertEqual([h.text for h in headings], ["Out"])

    def test_links_in_code_spans_are_ignored(self):
        _, links = mdwarden.parse_markdown("Use `[a](b.md)` but see [c](d.md).\n")
        self.assertEqual([link.target for link in links], ["d.md"])

    def test_image_title_and_angle_brackets(self):
        text = '![logo](img/logo.png "Logo") and [two](<my page.md>)\n'
        _, links = mdwarden.parse_markdown(text)
        self.assertEqual(links[0].kind, "image")
        self.assertEqual(links[0].target, "img/logo.png")
        self.assertEqual(links[1].target, "my page.md")

    def test_crlf_keeps_line_numbers(self):
        text = "# One\r\n\r\n[bad](gone.md)\r\n"
        _, links = mdwarden.parse_markdown(text)
        self.assertEqual(links[0].line, 3)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self, files, **kwargs):
        write_tree(self.root, files)
        return mdwarden.run_check(self.root, **kwargs)

    def test_clean_project_has_no_findings(self):
        _, findings = self.run_check({
            "README.md": "# Home\n\nSee [guide](guide.md#setup).\n",
            "guide.md": "# Guide\n\n## Setup\n\nBack to [home](README.md#home).\n",
        })
        self.assertEqual(findings, [])

    def test_broken_relative_link(self):
        _, findings = self.run_check({"README.md": "# Home\n\n[x](missing.md)\n"})
        self.assertEqual(codes(findings), [("README.md", 3, "broken-link")])

    def test_percent_encoded_path_is_resolved(self):
        _, findings = self.run_check({
            "README.md": "# Home\n\n[doc](my%20doc.md)\n",
            "my doc.md": "# Doc\n",
        })
        self.assertEqual(findings, [])

    def test_broken_cross_file_anchor(self):
        _, findings = self.run_check({
            "README.md": "# Home\n\n[x](guide.md#nope)\n",
            "guide.md": "# Guide\n",
        })
        self.assertEqual(codes(findings), [("README.md", 3, "broken-anchor")])

    def test_same_page_anchor_checked(self):
        _, findings = self.run_check({"README.md": "# Home\n\n[x](#missing)\n"})
        self.assertEqual(codes(findings), [("README.md", 3, "broken-anchor")])

    def test_empty_link_target(self):
        _, findings = self.run_check({"README.md": "# Home\n\n[x]()\n"})
        self.assertEqual(codes(findings), [("README.md", 3, "empty-link")])

    def test_https_without_host_is_bad_url(self):
        _, findings = self.run_check({"README.md": "# Home\n\n[x](https://)\n"})
        self.assertEqual(codes(findings), [("README.md", 3, "bad-url")])

    def test_mailto_and_real_urls_pass_without_network(self):
        _, findings = self.run_check({
            "README.md": "# Home\n\n[a](mailto:me@example.com) [b](https://example.com/x)\n",
        })
        self.assertEqual(findings, [])

    def test_heading_skip_reported(self):
        _, findings = self.run_check({"README.md": "# Home\n\n#### Deep\n"})
        self.assertEqual(codes(findings), [("README.md", 3, "heading-skip")])

    def test_empty_heading_and_duplicate_heading_warn(self):
        _, findings = self.run_check({"README.md": "# Home\n\n## Dup\n\n## Dup\n\n#\n"})
        found = {f.code for f in findings}
        self.assertIn("duplicate-heading", found)
        self.assertIn("empty-heading", found)

    def test_orphan_detected_and_entries_exempt(self):
        files, findings = self.run_check({
            "README.md": "# Home\n",
            "lonely.md": "# Lonely\n",
        })
        self.assertEqual(codes(findings), [("lonely.md", 1, "orphan")])
        other = os.path.join(self.root, "explicit")
        write_tree(other, {"lonely.md": "# Lonely\n"})
        _, findings = mdwarden.run_check(other, entries=["lonely.md"])
        self.assertEqual(findings, [])

    def test_ignore_glob_skips_files(self):
        _, findings = self.run_check({
            "README.md": "# Home\n\n[x](drafts/todo.md)\n",
            "drafts/todo.md": "# Todo\n\n[y](nowhere.md)\n",
        }, ignore=["drafts/*"])
        self.assertEqual(findings, [])

    def test_hidden_folders_are_skipped(self):
        files, _ = self.run_check({
            "README.md": "# Home\n",
            ".cache/notes.md": "# Hidden\n",
        })
        self.assertEqual(files, ["README.md"])

    def test_link_out_of_project_is_warning(self):
        _, findings = self.run_check({"docs/a.md": "# A\n\n[x](../../outside.md)\n",
                                      "README.md": "# Home\n\n[a](docs/a.md)\n"})
        self.assertEqual(codes(findings), [("docs/a.md", 3, "outside-root")])

    def test_directory_link_is_accepted(self):
        _, findings = self.run_check({
            "README.md": "# Home\n\n[docs](docs/) and [a](docs/a.md)\n",
            "docs/a.md": "# A\n",
        })
        self.assertEqual(findings, [])


class TocTests(unittest.TestCase):
    def test_nested_and_limited_by_level(self):
        headings, _ = mdwarden.parse_markdown("# Top\n\n## Mid\n\n### Low\n\n#### Deep\n")
        lines = mdwarden.build_toc(headings, max_level=3)
        self.assertEqual(lines, [
            "- [Top](#top)",
            "  - [Mid](#mid)",
            "    - [Low](#low)",
        ])

    def test_replace_toc_between_markers(self):
        text = "# T\n<!-- toc:start -->\nold\n<!-- toc:end -->\n## S\n"
        updated = mdwarden.replace_toc(text, ["- [T](#t)"])
        self.assertEqual(updated, "# T\n<!-- toc:start -->\n- [T](#t)\n<!-- toc:end -->\n## S\n")

    def test_replace_toc_keeps_crlf(self):
        text = "<!-- toc:start -->\r\n<!-- toc:end -->\r\n"
        updated = mdwarden.replace_toc(text, ["- [A](#a)"])
        self.assertEqual(updated, "<!-- toc:start -->\r\n- [A](#a)\r\n<!-- toc:end -->\r\n")

    def test_replace_toc_requires_markers(self):
        with self.assertRaises(ValueError):
            mdwarden.replace_toc("# No markers\n", ["- x"])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = mdwarden.main(argv)
        return code, out.getvalue()

    def test_exit_zero_when_clean(self):
        write_tree(self.root, {"README.md": "# Home\n"})
        code, out = self.run_cli(["check", self.root])
        self.assertEqual(code, 0)
        self.assertIn("0 errors, 0 warnings", out)

    def test_exit_one_on_error(self):
        write_tree(self.root, {"README.md": "# Home\n\n[x](gone.md)\n"})
        code, out = self.run_cli(["check", self.root])
        self.assertEqual(code, 1)
        self.assertIn("README.md:3: error: [broken-link]", out)

    def test_fail_on_warning_catches_orphans(self):
        write_tree(self.root, {"README.md": "# Home\n", "notes.md": "# N\n"})
        code, _ = self.run_cli(["check", self.root, "--fail-on", "warning"])
        self.assertEqual(code, 1)
        code, _ = self.run_cli(["check", self.root, "--fail-on", "never"])
        self.assertEqual(code, 0)

    def test_json_format(self):
        write_tree(self.root, {"README.md": "# Home\n\n[x](gone.md)\n"})
        code, out = self.run_cli(["check", self.root, "--format", "json"])
        report = json.loads(out)
        self.assertEqual(report["errors"], 1)
        self.assertEqual(report["findings"][0]["code"], "broken-link")
        self.assertEqual(code, 1)

    def test_missing_root_is_usage_error(self):
        code, _ = self.run_cli(["check", os.path.join(self.root, "nope")])
        self.assertEqual(code, 2)

    def test_toc_write_updates_file(self):
        path = os.path.join(self.root, "page.md")
        write_tree(self.root, {"page.md": "<!-- toc:start -->\n<!-- toc:end -->\n# Alpha\n"})
        code, _ = self.run_cli(["toc", path, "--write"])
        self.assertEqual(code, 0)
        with open(path, encoding="utf-8") as handle:
            self.assertIn("- [Alpha](#alpha)", handle.read())

    def test_toc_write_without_markers_fails(self):
        path = os.path.join(self.root, "page.md")
        write_tree(self.root, {"page.md": "# Alpha\n"})
        code, _ = self.run_cli(["toc", path, "--write"])
        self.assertEqual(code, 2)

    def test_slug_command(self):
        code, out = self.run_cli(["slug", "Getting", "Started!"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "getting-started")


if __name__ == "__main__":
    unittest.main()
