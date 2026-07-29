"""الثوابت والأنواع — roles, tiers, pricing, and the auth request context.

مصدر واحد للحقيقة لحدود الأدوار والحصص والتسعير كي لا تتكرّر الأرقام في
النقاط النهائية. المال بالسنتات الصحيحة (لا عائم). Single source of truth.
"""
from __future__ import annotations

import dataclasses
import enum


class Role(str, enum.Enum):
    """أدوار النظام الثلاثة · the three system roles."""
    SILK_ADMIN = "silk_admin"      # داخلي: مقاييس مجمّعة، تمويل، مفتاح القتل
    SILK_ANALYST = "silk_analyst"  # داخلي: مجمّعات مجهّلة للقراءة فقط
    FACTORY = "factory"            # عميل: CRUD كامل على بيانات حسابه فقط


class Tier(str, enum.Enum):
    """طبقات الاشتراك · subscription tiers."""
    BASIC = "basic"
    SILVER = "silver"
    GOLD = "gold"
    PLATINUM = "platinum"


class Operation(str, enum.Enum):
    """أنواع عمليات الدفتر · ledger operation types (mirror the CHECK constraint)."""
    EMAIL_SENT = "email_sent"
    REPORT_GENERATED = "report_generated"
    WALLET_FUNDED = "wallet_funded"
    API_CALL = "api_call"
    STORAGE_CHARGE = "storage_charge"
    COMPARISON_REPORT = "comparison_report"
    DRAFT_EMAIL = "draft_email"


# ── حدود الطبقات · tier limits ───────────────────────────────────────────────
# monthly_studies: العدد المسموح شهرياً. Basic خاصّة: 1 مدى الحياة (لا تُصفَّر).
# users: -1 = غير محدود. funnel_max_studies: 0 = لا قمع، -1 = غير محدود.
@dataclasses.dataclass(frozen=True)
class TierLimits:
    monthly_studies: int
    lifetime_studies: int          # >0 فقط لـ Basic؛ 0 يعني «لا سقف مدى حياة»
    users: int                     # -1 = unlimited
    funnel: bool
    funnel_max_studies: int        # 0 = تعطيل، -1 = غير محدود
    dashboard: str                 # 'none' | 'basic' | 'full'
    api_access: bool
    white_label: bool
    export: bool
    price_cents_per_year: int


TIER_LIMITS: dict[Tier, TierLimits] = {
    Tier.BASIC: TierLimits(
        monthly_studies=0, lifetime_studies=1, users=1, funnel=False,
        funnel_max_studies=0, dashboard="none", api_access=False,
        white_label=False, export=False, price_cents_per_year=0),
    Tier.SILVER: TierLimits(
        monthly_studies=2, lifetime_studies=0, users=3, funnel=False,
        funnel_max_studies=0, dashboard="basic", api_access=False,
        white_label=False, export=False, price_cents_per_year=100_000),   # $1,000
    Tier.GOLD: TierLimits(
        monthly_studies=6, lifetime_studies=0, users=10, funnel=True,
        funnel_max_studies=10, dashboard="full", api_access=False,
        white_label=False, export=False, price_cents_per_year=500_000),   # $5,000
    Tier.PLATINUM: TierLimits(
        monthly_studies=15, lifetime_studies=0, users=-1, funnel=True,
        funnel_max_studies=-1, dashboard="full", api_access=True,
        white_label=True, export=True, price_cents_per_year=1_500_000),   # $15,000
}


def tier_limits(tier: str | Tier) -> TierLimits:
    """حدود طبقة — resolve a tier's limits (accepts str or Tier)."""
    t = tier if isinstance(tier, Tier) else Tier(str(tier))
    return TIER_LIMITS[t]


# ── التسعير (سنتات) · pricing in cents ───────────────────────────────────────
# قابلة للضبط لاحقاً؛ ثابتة الآن كي تكون الاختبارات قطعية.
PRICE_EMAIL_SENT_CENTS = 5           # $0.05 / email
PRICE_REPORT_CENTS = 100             # $1.00 / PDF report
PRICE_API_CALL_CENTS = 1             # over-quota Platinum API call
PRICE_STORAGE_CENTS_PER_GB_MONTH = 10  # $0.10 / GB-month


def projected_email_cost_cents(recipient_count: int) -> int:
    """التكلفة المتوقّعة لإطلاق دراسة — projected send cost for a study launch."""
    return max(0, int(recipient_count)) * PRICE_EMAIL_SENT_CENTS


# ── سياق المصادقة للطلب · per-request auth context ───────────────────────────
@dataclasses.dataclass(frozen=True)
class AuthContext:
    """الهويّة المحمَّلة في كل طلب — current_user / current_account / current_role.

    الوسيط (middleware) يبنيها من الجلسة ويضعها في `request.state.auth`؛ كل
    فحص صلاحية وكل استعلام مُستأجَر يشتقّ منها account_id — لا مصدر آخر.
    """
    user_id: int
    account_id: int
    role: Role
    email: str
    language_preference: str = "en"
    session_id: int | None = None

    @property
    def is_silk_admin(self) -> bool:
        return self.role == Role.SILK_ADMIN

    @property
    def is_silk_analyst(self) -> bool:
        return self.role == Role.SILK_ANALYST

    @property
    def is_factory(self) -> bool:
        return self.role == Role.FACTORY

    @property
    def is_internal(self) -> bool:
        """مستخدم سِلك داخلي (admin أو analyst) · a Silk-internal operator."""
        return self.role in (Role.SILK_ADMIN, Role.SILK_ANALYST)
