"""منصّة سِلك متعدّدة المستأجرين — Silk multi-tenant SaaS platform (PR-1).

طبقة SaaS مستقلّة فوق محرّك ذكاء السوق القائم: مصادقة، جلسات، حسابات، أدوار،
وعزل صارم بين المستأجرين — بقاعدة بيانات خاصّة بها (`SILK_PLATFORM_DB`) لا تمسّ
`data/silk.db`. كل الوحدات تستورد دون شبكة ودون fastapi (يُحمَّل بكسل في api.py).

Self-contained auth + tenancy foundation. Every module imports offline; FastAPI
is lazy-imported only inside create_platform_app().
"""
from __future__ import annotations

__all__ = ["db", "models", "passwords", "tokens", "crypto", "auth", "wallet",
           "quota", "settings", "audit", "repository", "email_queue", "jobs",
           "seed", "create_platform_app", "mount"]


def create_platform_app():
    """أنشئ تطبيق FastAPI مستقلّاً للمنصّة — build a standalone platform app."""
    from .api import create_platform_app as _factory
    return _factory()


def mount(app) -> bool:
    """ركّب موجّه المنصّة على تطبيق قائم — attach the platform router to `app`."""
    from .api import mount as _mount
    return _mount(app)
