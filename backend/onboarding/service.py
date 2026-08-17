"""Onboarding business logic — server-enforced state machine."""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.ai.report import verdict_for
from backend.onboarding.models import OnboardingStatus, STATUS_RANK, parse_status
from backend.onboarding.store import OnboardingStore, SellerProfile
from backend.wb_engine.sources.seller_api import check_seller_api_key

log = logging.getLogger("selleros.onboarding")


class OnboardingError(Exception):
    def __init__(self, code: str, message: str, *, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


class OnboardingService:
    """
    Telegram session → seller profile → WB → product → Argus → READY.

    seller_id always comes from the authenticated session, never the client body.
    """

    def __init__(
        self,
        *,
        store: Optional[OnboardingStore] = None,
        memory_store=None,
        product_service=None,
        analyzer=None,
        wb_check=None,
    ):
        self.store = store or OnboardingStore()
        self.memory = memory_store
        self.product_service = product_service
        self.analyzer = analyzer
        # Injectable for tests (mock WB ping).
        self._wb_check = wb_check or check_seller_api_key

    async def _memory(self):
        """Lazy-connect MemoryStore on the active event loop (TestClient-safe)."""
        if self.memory is None:
            return None
        if getattr(self.memory, "_db", None) is None:
            await self.memory.connect()
        return self.memory

    # ------------------------------------------------------------------ profile

    def ensure_from_session(self, session) -> SellerProfile:
        return self.store.ensure_profile(
            seller_id=str(session.seller_id),
            telegram_user_id=str(session.telegram_user_id),
            display_name=session.display_name,
        )

    async def get_state(self, session) -> dict[str, Any]:
        profile = self.ensure_from_session(session)
        seller_id = profile.seller_id
        wb_connected = self.store.has_wb_credentials(seller_id)

        first_article: Optional[int] = None
        has_product = False
        has_analysis = False

        if self.memory is not None:
            try:
                mem = await self._memory()
                uid = int(seller_id)
                products = await mem.list_products(uid)
                has_product = bool(products)
                if products:
                    first_article = int(products[0].article)
                analyses = await mem.list_analyses(uid, limit=1)
                has_analysis = bool(analyses)
            except Exception as exc:
                log.info("onboarding state memory skip: %s", type(exc).__name__)

        status = profile.onboarding_status
        steps = {
            "auth": True,
            "profile": True,
            "wb_connect": status != OnboardingStatus.NEW or wb_connected,
            "first_product": STATUS_RANK[status] >= STATUS_RANK[OnboardingStatus.FIRST_PRODUCT_ADDED],
            "first_analysis": status == OnboardingStatus.READY,
            "dashboard_ready": status == OnboardingStatus.READY,
        }
        return {
            "seller_id": seller_id,
            "display_name": profile.display_name,
            "status": status.value,
            "wb_connected": wb_connected and status != OnboardingStatus.NEW,
            "has_product": has_product,
            "has_analysis": has_analysis,
            "first_article": first_article,
            "steps": steps,
        }

    # ------------------------------------------------------------------ WB

    async def connect_wb(self, session, api_key: str) -> dict[str, Any]:
        profile = self.ensure_from_session(session)
        key = (api_key or "").strip()
        if not key:
            raise OnboardingError(
                "invalid_credentials",
                "API key required",
                http_status=400,
            )

        ok = await self._wb_check(key)
        if not ok:
            # Do not store invalid keys; never echo the key.
            raise OnboardingError(
                "invalid_credentials",
                "Wildberries API key rejected",
                http_status=400,
            )

        self.store.save_wb_credentials(profile.seller_id, key)

        # Promote NEW → WB_CONNECTED; reconnect keeps higher progress.
        if profile.onboarding_status == OnboardingStatus.NEW:
            profile = self.store.set_status(
                profile.seller_id, OnboardingStatus.WB_CONNECTED
            )

        return {
            "connected": True,
            "status": profile.onboarding_status.value,
            "error": None,
        }

    async def check_wb(self, session) -> dict[str, Any]:
        profile = self.ensure_from_session(session)
        key = self.store.get_wb_api_key(profile.seller_id)
        if not key:
            return {
                "connected": False,
                "status": profile.onboarding_status.value,
                "error": None,
            }

        ok = await self._wb_check(key)
        if not ok:
            return {
                "connected": False,
                "status": profile.onboarding_status.value,
                "error": "invalid_credentials",
            }

        # Successful check while NEW (edge: credentials without status) → promote.
        if profile.onboarding_status == OnboardingStatus.NEW:
            profile = self.store.set_status(
                profile.seller_id, OnboardingStatus.WB_CONNECTED
            )

        return {
            "connected": True,
            "status": profile.onboarding_status.value,
            "error": None,
        }

    async def disconnect_wb(self, session) -> dict[str, Any]:
        profile = self.ensure_from_session(session)
        revoked = self.store.delete_wb_credentials(profile.seller_id)
        # Reset onboarding progress; keep products / analyses in MemoryStore.
        profile = self.store.set_status(profile.seller_id, OnboardingStatus.NEW)
        return {
            "connected": False,
            "status": profile.onboarding_status.value,
            "revoked": revoked or True,
        }

    # -------------------------------------------------------------- product

    def _require_wb(self, profile: SellerProfile) -> None:
        if profile.onboarding_status == OnboardingStatus.NEW:
            raise OnboardingError(
                "wb_required",
                "Connect Wildberries before adding a product",
                http_status=409,
            )
        if not self.store.has_wb_credentials(profile.seller_id):
            raise OnboardingError(
                "wb_required",
                "Connect Wildberries before adding a product",
                http_status=409,
            )

    async def add_first_product(self, session, article: int) -> dict[str, Any]:
        profile = self.ensure_from_session(session)
        self._require_wb(profile)

        if self.product_service is None:
            raise OnboardingError(
                "product_unavailable",
                "Product service not configured",
                http_status=503,
            )
        mem = await self._memory()
        if mem is None:
            raise OnboardingError(
                "memory_unavailable",
                "Memory store not configured",
                http_status=503,
            )

        uid = int(profile.seller_id)
        existing = await mem.get_product(uid, int(article))
        if existing is not None:
            # Idempotent duplicate — do not regress status.
            if profile.onboarding_status == OnboardingStatus.WB_CONNECTED:
                profile = self.store.set_status(
                    profile.seller_id, OnboardingStatus.FIRST_PRODUCT_ADDED
                )
            return {
                "article": int(article),
                "title": existing.title,
                "status": profile.onboarding_status.value,
                "already_existed": True,
                "source": None,
            }

        product = await self.product_service.get_product("wildberries", int(article))
        if product is None:
            raise OnboardingError(
                "product_not_found",
                f"Product {article} not found",
                http_status=404,
            )

        photos = getattr(product, "photo_count", None)
        if photos is None:
            photos = len(getattr(product, "photos", None) or [])

        await mem.upsert_product(
            uid,
            int(article),
            "wildberries",
            title=getattr(product, "title", None) or "",
            price=getattr(product, "price", None),
            rating=getattr(product, "rating", None),
            score=None,
            photos=int(photos or 0),
            imt_id=getattr(product, "imt_id", None),
            root_id=getattr(product, "root_id", None),
        )

        if profile.onboarding_status == OnboardingStatus.WB_CONNECTED:
            profile = self.store.set_status(
                profile.seller_id, OnboardingStatus.FIRST_PRODUCT_ADDED
            )

        return {
            "article": int(article),
            "title": getattr(product, "title", None),
            "status": profile.onboarding_status.value,
            "already_existed": False,
            "source": getattr(product, "source", None),
        }

    # -------------------------------------------------------------- analyze

    def _require_product_step(self, profile: SellerProfile) -> None:
        rank = STATUS_RANK[parse_status(profile.onboarding_status)]
        if rank < STATUS_RANK[OnboardingStatus.FIRST_PRODUCT_ADDED]:
            raise OnboardingError(
                "product_required",
                "Add a product before first Argus analysis",
                http_status=409,
            )

    async def first_analyze(
        self,
        session,
        article: Optional[int] = None,
    ) -> dict[str, Any]:
        profile = self.ensure_from_session(session)
        self._require_product_step(profile)

        if self.product_service is None or self.analyzer is None:
            raise OnboardingError(
                "analyze_unavailable",
                "Analyzer not configured",
                http_status=503,
            )
        mem = await self._memory()
        if mem is None:
            raise OnboardingError(
                "analyze_unavailable",
                "Analyzer not configured",
                http_status=503,
            )

        uid = int(profile.seller_id)
        target = article
        if target is None:
            products = await mem.list_products(uid)
            if not products:
                raise OnboardingError(
                    "product_required",
                    "No product to analyze",
                    http_status=409,
                )
            target = int(products[0].article)

        # Idempotent-ish: already READY with an analysis for this article.
        if profile.onboarding_status == OnboardingStatus.READY:
            analyses = await mem.list_analyses(uid, limit=20)
            for a in analyses:
                if int(a.article) == int(target):
                    return {
                        "article": int(target),
                        "score": int(a.score),
                        "verdict": a.verdict or "",
                        "status": OnboardingStatus.READY.value,
                        "already_analyzed": True,
                    }

        product = await self.product_service.get_product("wildberries", int(target))
        if product is None:
            # Fall back to memory snapshot for offline/idempotent re-run.
            snap = await mem.get_product(uid, int(target))
            if snap is None:
                raise OnboardingError(
                    "product_not_found",
                    f"Product {target} not found",
                    http_status=404,
                )
            from backend.wb.cdn_provider import WBProduct

            product = WBProduct(
                article=int(target),
                title=snap.title,
                price=snap.price,
                rating=snap.rating,
                photo_count=int(snap.photos or 0),
                source="history",
            )

        result = await self.analyzer.analyze(product, with_ai=False)
        score = int(result["score"])
        verdict = verdict_for(score)

        await mem.add_analysis(
            uid,
            int(target),
            "wildberries",
            title=getattr(product, "title", None) or "",
            price=getattr(product, "price", None),
            score=score,
            verdict=verdict,
        )
        await mem.upsert_product(
            uid,
            int(target),
            "wildberries",
            title=getattr(product, "title", None) or "",
            price=getattr(product, "price", None),
            rating=getattr(product, "rating", None),
            score=score,
            photos=int(
                getattr(product, "photo_count", None)
                or len(getattr(product, "photos", None) or [])
                or 0
            ),
            imt_id=getattr(product, "imt_id", None),
            root_id=getattr(product, "root_id", None),
        )

        profile = self.store.set_status(profile.seller_id, OnboardingStatus.READY)
        return {
            "article": int(target),
            "score": score,
            "verdict": verdict,
            "status": profile.onboarding_status.value,
            "already_analyzed": False,
        }
