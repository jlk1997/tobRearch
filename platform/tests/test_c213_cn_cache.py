"""中文题录带上知网号；作者作品走文件缓存；没有密钥不打 Semantic Scholar。"""

from fengsuan.app.services.web.cn import paper_from_hit
from fengsuan.app.services.web import scholarly


def test_cn_hit_keeps_filename_and_doi():
    row = paper_from_hit(
        {
            "title": "图像水印综述",
            "url": "https://kns.cnki.net/kcms/detail/detail.aspx?filename=JSJX202401001",
            "snippet": "综述",
        }
    )
    assert row["cnki_id"] == "JSJX202401001"
    assert row["title"] == "图像水印综述"
    linked = paper_from_hit({"title": "另一篇", "url": "https://example.org/10.1000/xyz.1"})
    assert linked["doi"] == "10.1000/xyz.1"


def test_author_works_reuse_file_cache(monkeypatch):
    calls = {"n": 0}

    def _json(*_a, **_k):
        calls["n"] += 1
        return {"results": [], "meta": {"count": 12}}

    monkeypatch.setattr(scholarly, "_json", _json)
    monkeypatch.setattr(scholarly, "cache_get", lambda *a, **k: None, raising=False)
    stored = {}

    def _get(kind, key, ttl):
        return stored.get(key)

    def _put(kind, key, data):
        stored[key] = data

    import fengsuan.app.services.web.common as common

    monkeypatch.setattr(common, "cache_get", _get)
    monkeypatch.setattr(common, "cache_put", _put)
    first = scholarly.openalex_author_works("A1", limit=5)
    second = scholarly.openalex_author_works("A1", limit=5)
    assert first["total"] == 12
    assert second["total"] == 12
    assert calls["n"] == 1


def test_s2_citing_stays_empty_without_key(monkeypatch):
    monkeypatch.delenv("S2_API_KEY", raising=False)
    assert scholarly.s2_citing("10.1000/xyz") == []
