# -*- coding: utf-8 -*-
"""Automated HTML tag-balance and syntax integrity test for all Jinja templates.

Prevents silent DOM swallowing bugs caused by unclosed tags or structural mismatches.
"""
import os
import re
import unittest
from html.parser import HTMLParser


class TemplateTagChecker(HTMLParser):
    VOID_ELEMENTS = {
        'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
        'link', 'meta', 'param', 'source', 'track', 'wbr'
    }

    def __init__(self):
        super().__init__()
        self.stack = []
        self.mismatches = []
        self.unexpected_closers = []

    def handle_starttag(self, tag, attrs):
        tag_lower = tag.lower()
        if tag_lower not in self.VOID_ELEMENTS:
            line, col = self.getpos()
            attr_dict = dict(attrs)
            el_id = attr_dict.get('id', '')
            el_cls = attr_dict.get('class', '')
            self.stack.append((tag_lower, line, col, el_id, el_cls))

    def handle_endtag(self, tag):
        tag_lower = tag.lower()
        if tag_lower in self.VOID_ELEMENTS:
            return
        if not self.stack:
            self.unexpected_closers.append((tag_lower, self.getpos()[0]))
            return
        last_tag, last_line, last_col, last_id, last_cls = self.stack.pop()
        if last_tag != tag_lower:
            self.mismatches.append({
                'expected': last_tag,
                'opened_at': last_line,
                'opened_id': last_id,
                'got': tag_lower,
                'closed_at': self.getpos()[0]
            })


def check_template(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Strip jinja comments, statements, and expressions
    clean = re.sub(r'\{#.*?#\}', '', content, flags=re.DOTALL)
    clean = re.sub(r'\{%.*?%\}', '', clean, flags=re.DOTALL)
    clean = re.sub(r'\{\{.*?\}\}', '', clean, flags=re.DOTALL)
    # Strip script and style bodies to avoid raw < > tokens
    clean = re.sub(r'<script\b[^>]*>.*?</script>', '', clean, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<style\b[^>]*>.*?</style>', '', clean, flags=re.DOTALL | re.IGNORECASE)

    parser = TemplateTagChecker()
    parser.feed(clean)
    return parser


class TemplateTagBalanceTestCase(unittest.TestCase):
    def setUp(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.templates_dir = os.path.join(base_dir, 'templates')

    def test_all_templates_have_balanced_tags(self):
        errors = []
        tested_count = 0

        for root, _, files in os.walk(self.templates_dir):
            for fname in files:
                if not fname.endswith('.html'):
                    continue
                file_path = os.path.join(root, fname)
                rel_path = os.path.relpath(file_path, self.templates_dir)
                parser = check_template(file_path)
                tested_count += 1

                if parser.stack:
                    details = "\n".join(
                        f"    - <{t[0]}> (opened at line {t[1]}, id='{t[3]}')"
                        for t in parser.stack
                    )
                    errors.append(f"Unclosed tag(s) in '{rel_path}':\n{details}")

                if parser.mismatches:
                    details = "\n".join(
                        f"    - Expected </{m['expected']}> (opened at line {m['opened_at']} id='{m['opened_id']}'), got </{m['got']}> at line {m['closed_at']}"
                        for m in parser.mismatches
                    )
                    errors.append(f"Tag mismatch(es) in '{rel_path}':\n{details}")

                if parser.unexpected_closers:
                    details = "\n".join(
                        f"    - Unexpected </{u[0]}> at line {u[1]}"
                        for u in parser.unexpected_closers
                    )
                    errors.append(f"Unexpected closing tag(s) in '{rel_path}':\n{details}")

        self.assertGreater(tested_count, 10, "Template directory should have templates to test")
        if errors:
            self.fail("\n\n" + "\n\n".join(errors))

    def test_all_templates_compile_with_jinja(self):
        """Verifies that every HTML template in templates/ compiles cleanly with Jinja2."""
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(self.templates_dir))
        compile_errors = []
        for root, _, files in os.walk(self.templates_dir):
            for fname in files:
                if not fname.endswith('.html'):
                    continue
                rel_path = os.path.relpath(os.path.join(root, fname), self.templates_dir).replace('\\', '/')
                try:
                    env.get_template(rel_path)
                except Exception as e:
                    compile_errors.append(f"Jinja compilation failed for '{rel_path}': {e}")
        if compile_errors:
            self.fail("\n\n" + "\n\n".join(compile_errors))


if __name__ == '__main__':
    unittest.main()
