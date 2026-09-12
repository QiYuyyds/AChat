"""Tests for digest bucket constants, validator, and path helpers."""

from __future__ import annotations

from app.memory.buckets import (
    DIGEST_BUCKETS,
    make_stable_key,
    normalize_bucket,
    to_relative_memory_path,
    validate_and_normalize_unit,
)
from app.memory.file_store.file_catalog import FileCatalog
from app.memory.file_store.frontmatter import MemoryFrontmatter


def test_digest_buckets_include_personal():
    assert DIGEST_BUCKETS == ("procedure", "personal", "wiki")


def test_normalize_bucket_defaults_unknown_to_wiki():
    assert normalize_bucket("personal") == "personal"
    assert normalize_bucket("nope") == "wiki"
    assert normalize_bucket(None) == "wiki"


def test_validator_drops_delivery_personal():
    unit = {
        "name": "零依赖原生 JS Todo List 应用交付记录",
        "bucket": "personal",
        "summary": "已交付并 in_review，部署预览 /deployments/dep_xxx",
        "content": "功能清单与改动文件：index.html...",
        "importance": 0.5,
        "source_paths": [],
    }
    assert validate_and_normalize_unit(unit) is None


def test_validator_shortens_encyclopedia_personal():
    unit = {
        "name": "洛小阳匠人宇宙",
        "bucket": "personal",
        "summary": "用户关注洛小阳匠人宇宙",
        "content": (
            "## Rule / fact\n用户关注洛小阳\n\n"
            "## How to apply\n"
            "- 想立刻读过瘾先看诡匠\n"
            "- 《诡匠》100.58 万字 / 379 章，收藏 13 万\n"
            "- 必听代表作列表很长\n"
        ),
        "importance": 0.7,
        "tags": ["洛小阳"],
        "source_paths": ["daily/2026-08-05/x.md"],
    }
    out = validate_and_normalize_unit(unit)
    assert out is not None
    assert out["bucket"] == "personal"
    assert "万字" not in out["content"]
    assert out["stable_key"].startswith("user.")


def test_validator_rebuckets_runbook_personal_to_procedure():
    unit = {
        "name": "PowerShell 引号规避",
        "bucket": "personal",
        "summary": "复杂引号用临时脚本",
        "content": "## Trigger\n复杂引号\n\n## Steps\n1. 写临时 ps1\n2. 执行\n",
        "importance": 0.6,
        "source_paths": [],
    }
    out = validate_and_normalize_unit(unit)
    assert out is not None
    assert out["bucket"] == "procedure"


def test_stable_key_identity_and_interest():
    assert make_stable_key("personal", "用户身份：重庆邮电大学实验室负责人") == (
        "user.identity.profile"
    )
    # Topic-level: object suffix (doudou) is stripped for personal bucket
    assert make_stable_key("personal", "音乐兴趣：关注 DOUDOU 偏现场版") == (
        "user.interest.music"
    )
    assert make_stable_key("wiki", "甘肃旅行指南").startswith("wiki.")


def test_relative_memory_path_strips_absolute():
    abs_path = r"D:\bitdance-agenthub-main\.agenthub-data\memory\daily\2026-08-05\a.md"
    assert to_relative_memory_path(abs_path).startswith("daily/")
    assert to_relative_memory_path("digest/personal/x.md") == "digest/personal/x.md"


def test_frontmatter_accepts_personal_and_stable_key():
    fm = MemoryFrontmatter.from_dict(
        {
            "name": "用户身份",
            "bucket": "personal",
            "importance": 0.8,
        }
    )
    assert fm.bucket == "personal"
    assert fm.stable_key == "user.identity.profile"
    assert fm.validate() == []


def test_file_catalog_bucket_for_personal(tmp_path):
    cat = FileCatalog(tmp_path / "catalog.db")
    cat.initialize()
    daily = tmp_path / "daily"
    digest = tmp_path / "digest"
    personal = digest / "personal"
    personal.mkdir(parents=True)
    f = personal / "a.md"
    f.write_text("# a\n", encoding="utf-8")
    assert cat._bucket_for_path(f, daily, digest) == "personal"
    cat.close()
