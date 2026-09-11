# -*- coding: utf-8 -*-
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from growth_ocr.parse import CellResult  # noqa: E402
from growth_ocr.template import CHECK, load_template  # noqa: E402


@pytest.fixture(scope="session")
def tpl():
    return load_template()


@pytest.fixture
def blank_results(tpl):
    """全部空白的 CellResult 清單，測 parse 時只要改要測的那幾格。"""

    def build():
        out = []
        for cell in tpl.cells:
            if cell.kind == CHECK:
                out.append(CellResult(path=cell.path, char=None, conf=1.0, ink=0.0, blank=True, kind=CHECK))
            elif cell.preprinted is not None:
                out.append(
                    CellResult(
                        path=cell.path,
                        char=cell.preprinted,
                        conf=1.0,
                        ink=0.5,
                        blank=False,
                        preprinted=cell.preprinted,
                    )
                )
            else:
                out.append(CellResult(path=cell.path, char=None, conf=0.0, ink=0.0, blank=True))
        return out

    return build


@pytest.fixture
def write(tpl):
    """把一串數字寫進某個 group（'' 代表留白），信心預設 0.99。"""

    def _write(results, group, text, conf=0.99, skip_first=False):
        index = {r.path: r for r in results}
        cells = tpl.group(group)
        if skip_first:
            cells = cells[1:]
        for cell, ch in zip(cells, text):
            r = index[cell.path]
            if ch == " ":
                r.char, r.conf, r.ink, r.blank = None, 0.0, 0.0, True
            else:
                r.char, r.conf, r.ink, r.blank = ch, conf, 0.1, False
        return results

    return _write
