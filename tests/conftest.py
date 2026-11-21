import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="localgaz-test-")
os.environ["LOCALGAZ_DB"] = os.path.join(_TMP, "test.db")  # 须在导入 app 前设置

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402

db.init_db()

DUP_PAGE = {
    "page_ref": "卷一·頁三",
    "old_text": "張三還書，張三攜酒。",
    "new_text": "張三還書，張三張三攜酒。",
    "annotations": [
        {"name": "張三", "start": 0, "end": 2},
        {"name": "張三", "start": 5, "end": 7},
    ],
}

SWAP_PAGE = {
    "page_ref": "卷二·頁七",
    "old_text": "張三和李四",
    "new_text": "李四和張三",
    "annotations": [
        {"name": "張三", "start": 0, "end": 2},
        {"name": "李四", "start": 3, "end": 5},
    ],
}


@pytest.fixture(autouse=True)
def clean_db():
    conn = db.connect()
    conn.executescript(
        "DELETE FROM confirmations; DELETE FROM annotations; DELETE FROM pages;")
    conn.commit()
    conn.close()
    yield


@pytest.fixture
def client():
    return TestClient(app)
