# -*- coding: utf-8 -*-
"""API 契約測試：完全用 stub 辨識器，不載任何模型。"""
from __future__ import annotations

import base64
import json
import random
import re

import pytest
from fastapi.testclient import TestClient

from growth_ocr import synth
from growth_ocr.api import create_app
from growth_ocr.config import Settings
from growth_ocr.recognizers.base import StubRecognizer

TODAY = "2026-09-10"


def truth_recognizer(tpl, form):
    """依合成表單的正解回答；順序＝pipeline 送格子的順序（template 順序中有墨水者）。"""
    order = [c.path for c in tpl.recognizable_cells if form.cells.get(c.path)]
    answers = {p: (form.cells[p], 0.99) for p in order}
    return StubRecognizer(answers=answers, order=order)


@pytest.fixture
def form(tpl):
    return synth.make_form(tpl, random.Random(2026))


@pytest.fixture
def client(tpl, form):
    app = create_app(Settings(backend="stub", static_dir=None), recognizer=truth_recognizer(tpl, form))
    with TestClient(app) as c:
        yield c


def data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def test_config_shape(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body["growthToolUrl"] == "/tool.html"
    assert body["queueEnabled"] is False
    assert body["backend"] == "stub"
    assert body["authRequired"] is False  # 沒設 CLINIC_KEY ＝ LAN 模式
    assert body["pilot"] is False  # 沒設 PILOT_DIR ＝ 不留底


def test_ocr_json_contract(client, form, tpl):
    r = client.post("/api/ocr", json={"image": data_url(form.jpeg), "today": TODAY})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"data", "backend", "ms"}
    assert body["backend"] == "stub"
    assert isinstance(body["ms"], int)

    d = body["data"]
    assert set(d) == {
        "seq",
        "gender",
        "birthDate",
        "fatherHeight",
        "motherHeight",
        "measurements",
        "uncertain",
        "notes",
    }
    expected = form.values.to_expected(tpl, TODAY)
    for key in ("seq", "gender", "birthDate", "fatherHeight", "motherHeight"):
        assert d[key] == expected[key], key
    assert d["measurements"] == expected["measurements"]
    assert d["uncertain"] == expected["uncertain"]
    assert isinstance(d["notes"], str)
    assert body["debug"]["cells"][0]["path"] == "seq[0]"


def test_full_form_keeps_leading_zero_seq_and_ten_rows(tpl):
    """端到端最壞情況：171 個數字格全填、病歷號帶前導零，切格與契約都要對得上。"""
    rng = random.Random(20260910)
    values = synth.random_values(rng, tpl, full=True)
    values.seq = "0012345"
    form = synth.make_form(tpl, rng, values=values)
    app = create_app(Settings(backend="stub", static_dir=None), recognizer=truth_recognizer(tpl, form))
    with TestClient(app) as c:
        r = c.post("/api/ocr", json={"image": data_url(form.jpeg), "today": TODAY})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["seq"] == "0012345"
    assert len(d["measurements"]) == tpl.row_count == 10
    assert d["uncertain"] == []
    assert len(r.json()["debug"]["cells"]) == len(tpl.digit_cells) == 171


def test_ocr_multipart(client, form):
    r = client.post(
        "/api/ocr",
        files={"file": ("form.jpg", form.jpeg, "image/jpeg")},
        data={"today": TODAY},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["gender"] in ("male", "female")


def test_bad_image_returns_400(client):
    r = client.post("/api/ocr", json={"image": "data:image/jpeg;base64,####"})
    assert r.status_code == 400
    assert "error" in r.json()


def test_missing_image_returns_400(client):
    assert client.post("/api/ocr", json={}).status_code == 400


def test_no_registration_marks_gives_retake_hint(client):
    import cv2
    import numpy as np

    blank = np.full((1200, 900, 3), 240, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", blank)
    assert ok
    r = client.post("/api/ocr", json={"image": data_url(buf.tobytes())})
    assert r.status_code == 400
    assert "定位塊" in r.json()["error"]


def test_clinic_key_enforced_when_set(tpl, form):
    app = create_app(
        Settings(backend="stub", static_dir=None, clinic_key="s3cret"),
        recognizer=truth_recognizer(tpl, form),
    )
    with TestClient(app) as c:
        assert c.get("/api/config").status_code == 401
        assert c.post("/api/ocr", json={"image": data_url(form.jpeg)}).status_code == 401
        r = c.get("/api/config", headers={"x-clinic-key": "s3cret"})
        assert r.status_code == 200 and r.json()["authRequired"] is True
        r = c.post(
            "/api/ocr",
            json={"image": data_url(form.jpeg), "today": TODAY},
            headers={"x-clinic-key": "s3cret"},
        )
        assert r.status_code == 200


def test_static_files_are_served(tpl, form):
    from growth_ocr.config import load_settings

    settings = load_settings()
    if not settings.static_dir:
        pytest.skip("找不到 pwa/public")
    app = create_app(
        Settings(backend="stub", static_dir=settings.static_dir),
        recognizer=truth_recognizer(tpl, form),
    )
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200
        for path in ("/index.html", "/tool.html", "/room.html"):
            assert c.get(path).status_code == 200, path


# --- 試用模式（PILOT_DIR）------------------------------------------------


@pytest.fixture
def pilot_client(tpl, form, tmp_path):
    pilot_dir = tmp_path / "pilot"
    app = create_app(
        Settings(backend="stub", static_dir=None, pilot_dir=pilot_dir),
        recognizer=truth_recognizer(tpl, form),
    )
    with TestClient(app) as c:
        yield c, pilot_dir


def test_pilot_config_reports_enabled(pilot_client):
    c, _ = pilot_client
    assert c.get("/api/config").json()["pilot"] is True


def test_pilot_ocr_stores_image_and_result(pilot_client, form):
    c, pilot_dir = pilot_client
    r = c.post("/api/ocr", json={"image": data_url(form.jpeg), "today": TODAY})
    assert r.status_code == 200, r.text
    body = r.json()
    pid = body["id"]
    assert re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{4}", pid), pid

    assert (pilot_dir / f"{pid}.jpg").read_bytes() == form.jpeg  # 原始位元組，不重新編碼
    stored = json.loads((pilot_dir / f"{pid}.ocr.json").read_text(encoding="utf-8"))
    assert stored["data"] == body["data"]
    assert stored["backend"] == "stub" and isinstance(stored["ms"], int)
    assert stored["debug"]["cells"] and stored["debug"]["quality"]


def test_pilot_stores_debug_even_when_response_omits_it(tpl, form, tmp_path):
    """OCR_DEBUG=0 只是不回給前端；留底仍要有 cells，否則報表算不出欄位層級結果。"""
    pilot_dir = tmp_path / "pilot"
    app = create_app(
        Settings(backend="stub", static_dir=None, include_debug=False, pilot_dir=pilot_dir),
        recognizer=truth_recognizer(tpl, form),
    )
    with TestClient(app) as c:
        body = c.post("/api/ocr", json={"image": data_url(form.jpeg), "today": TODAY}).json()
    assert "debug" not in body
    stored = json.loads((pilot_dir / f"{body['id']}.ocr.json").read_text(encoding="utf-8"))
    assert stored["debug"]["cells"]


def test_pilot_geometry_failure_is_also_stored(pilot_client):
    import cv2
    import numpy as np

    c, pilot_dir = pilot_client
    blank = np.full((1200, 900, 3), 240, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", blank)
    assert ok
    raw = buf.tobytes()
    r = c.post("/api/ocr", json={"image": data_url(raw)})
    assert r.status_code == 400
    pid = r.json()["id"]
    assert (pilot_dir / f"{pid}.jpg").read_bytes() == raw
    assert "定位塊" in json.loads((pilot_dir / f"{pid}.error.json").read_text(encoding="utf-8"))["error"]
    assert not (pilot_dir / f"{pid}.ocr.json").exists()


def test_pilot_confirm_stores_final_values(pilot_client, form):
    c, pilot_dir = pilot_client
    pid = c.post("/api/ocr", json={"image": data_url(form.jpeg), "today": TODAY}).json()["id"]
    final = {"seq": "0012345", "gender": "male", "birthDate": "2019-03-05",
             "fatherHeight": 175, "motherHeight": None,
             "measurements": [{"measureDate": TODAY, "height": 123.5, "weight": 24.0}]}
    r = c.post("/api/confirm", json={"id": pid, "data": final})
    assert r.status_code == 200 and r.json() == {"stored": True}
    stored = json.loads((pilot_dir / f"{pid}.confirmed.json").read_text(encoding="utf-8"))
    assert stored["id"] == pid and stored["data"] == final


def test_confirm_without_pilot_dir_is_not_an_error(client):
    r = client.post("/api/confirm", json={"id": "20260911T103012Z-a1f3", "data": {"seq": "1"}})
    assert r.status_code == 200 and r.json() == {"stored": False}


@pytest.mark.parametrize(
    "bad_id",
    ["../escape", "2026/09/11", "a b", "", "id.with.dot", "id_with_underscore", "trailing-newline\n", 42],
)
def test_confirm_rejects_unsafe_id(pilot_client, bad_id):
    c, pilot_dir = pilot_client
    r = c.post("/api/confirm", json={"id": bad_id, "data": {"seq": "1"}})
    assert r.status_code == 400, r.text
    assert not list(pilot_dir.glob("*.confirmed.json"))


def test_confirm_requires_data_object(pilot_client):
    c, _ = pilot_client
    assert c.post("/api/confirm", json={"id": "20260911T103012Z-a1f3"}).status_code == 400


def test_confirm_honours_clinic_key(tpl, form, tmp_path):
    app = create_app(
        Settings(backend="stub", static_dir=None, clinic_key="s3cret", pilot_dir=tmp_path / "pilot"),
        recognizer=truth_recognizer(tpl, form),
    )
    with TestClient(app) as c:
        payload = {"id": "20260911T103012Z-a1f3", "data": {"seq": "1"}}
        assert c.post("/api/confirm", json=payload).status_code == 401
        r = c.post("/api/confirm", json=payload, headers={"x-clinic-key": "s3cret"})
        assert r.status_code == 200 and r.json()["stored"] is True


def test_pilot_write_failure_does_not_break_ocr(tpl, form, tmp_path, monkeypatch):
    """留底是附加價值：磁碟滿了、目錄唯讀，護理師照樣要拿到判讀結果。"""
    from growth_ocr import api as api_mod

    monkeypatch.setattr(api_mod, "_pilot_write", lambda *a, **k: (_ for _ in ()).throw(OSError("磁碟已滿")))
    app = create_app(
        Settings(backend="stub", static_dir=None, pilot_dir=tmp_path / "pilot"),
        recognizer=truth_recognizer(tpl, form),
    )
    with TestClient(app) as c:
        r = c.post("/api/ocr", json={"image": data_url(form.jpeg), "today": TODAY})
    assert r.status_code == 200 and r.json()["data"]["gender"] in ("male", "female")
