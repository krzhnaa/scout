from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import (
    BackgroundTasks,
    FastAPI,
    HTTPException,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from .actions import fill_and_submit_form
from .agent import AgentSession
from .config import settings
from .notify import send_discord
from .email import send_demo_confirmation
from .payments import (
    PaymentError,
    amount_in_subunits,
    ensure_currency_enabled,
    settlement_currency,
    create_order,
    is_absolute_http_url,
    option_is_purchasable,
    retrieve_payment_and_order,
    verify_checkout_signature,
    verify_remote_payment,
    verify_webhook_signature,
)
from .currency import convert_to_settlement_currency
from .report import build_pdf


# =========================================================
# APP
# =========================================================

app = FastAPI(
    title="Scout Agent API",
    version="2.0.0",
)


# =========================================================
# CORS
# =========================================================

origins = [
    origin.strip()
    for origin in settings.FRONTEND_ORIGIN.split(",")
    if origin.strip()
]

# Keep wildcard only when no explicit frontend origin was supplied.
if not origins:
    origins = ["*"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=[
        "GET",
        "POST",
    ],
    allow_headers=["*"],
)


# =========================================================
# REPORTS
# =========================================================

app.mount(
    "/reports",
    StaticFiles(
        directory=settings.REPORTS_DIR
    ),
    name="reports",
)


# =========================================================
# SESSIONS
# =========================================================

SESSIONS: dict[str, AgentSession] = {}

# Prototype-only idempotency store. This intentionally has no durability across
# restarts; move it to Redis/Postgres before a production launch.
PAYMENT_TRANSACTIONS: dict[str, dict[str, Any]] = {}
PAYMENT_IDS: dict[str, str] = {}
PAYMENT_LOCK = asyncio.Lock()


# =========================================================
# REQUEST MODELS
# =========================================================

class StartRequest(BaseModel):
    """
    Request used to start a Scout run.

    Default behaviour is RESEARCH ONLY.

    Scout will never submit a form unless:
        action_mode == "form_fill"
        AND
        confirm_action == True
        AND
        target_url is provided
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    goal: str = Field(
        min_length=3,
        max_length=4000,
    )

    name: str = Field(
        default="Scout Demo",
        max_length=200,
    )

    email: str = Field(
        default="scout-demo@example.com",
        max_length=320,
    )

    action_mode: Literal[
        "research",
        "form_fill",
    ] = "research"

    target_url: str | None = Field(
        default=None,
        max_length=2000,
    )

    confirm_action: bool = False

    @field_validator(
        "goal",
        "name",
        "email",
    )
    @classmethod
    def strip_text(
        cls,
        value: str,
    ) -> str:
        return value.strip()


class StartResponse(BaseModel):
    session_id: str


class CreateOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=100)
    option_name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)

    @field_validator("session_id", "option_name")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class VerifyPaymentRequest(CreateOrderRequest):
    razorpay_order_id: str = Field(min_length=1, max_length=200)
    razorpay_payment_id: str = Field(min_length=1, max_length=200)
    razorpay_signature: str = Field(min_length=1, max_length=200)


class InvoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=100)
    razorpay_order_id: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)

    @field_validator("session_id", "razorpay_order_id", "email")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("enter a valid email address")
        return value


def _get_purchase_option(session: AgentSession, option_name: str) -> dict[str, Any]:
    decision = session.decision or {}
    options = decision.get("options", []) if isinstance(decision, dict) else []
    option = next((item for item in options if isinstance(item, dict) and item.get("name") == option_name), None)
    if option is None:
        raise HTTPException(status_code=404, detail="option not found in this session")
    option = dict(option)
    # Razorpay is test-mode only: a candidate with a verified researched price
    # can use its evidence page as the transaction reference even when research
    # did not find a separate retailer checkout URL. This never contacts the
    # retailer; it only enables Scout's own test checkout and invoice flow.
    if (
        not is_absolute_http_url(option.get("purchase_url"))
        and bool(option.get("price_verified"))
        and is_absolute_http_url(option.get("source_url"))
    ):
        option["purchase_url"] = option["source_url"]
        option["purchasable"] = True
    if not option_is_purchasable(option):
        raise HTTPException(status_code=400, detail="this option does not have verified purchase evidence")
    return option


def _transaction_response(transaction: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": True,
        "demo_mode": True,
        "option_name": transaction["option_name"],
        "price": transaction["price"],
        "currency": transaction["currency"],
        "researched_price": transaction.get("researched_price", transaction["price"]),
        "researched_currency": transaction.get("researched_currency", transaction["currency"]),
        "fx_rate": transaction.get("fx_rate"),
        "payment_id": transaction.get("payment_id"),
        "order_id": transaction["order_id"],
        "purchase_url": transaction["purchase_url"],
        "purchase_type": transaction["purchase_type"],
        "provider": transaction.get("provider", ""),
        "transaction_details": transaction.get("transaction_details", []),
        "email_sent": transaction.get("email_sent", False),
        "email": transaction.get("invoice_email") or None,
        "message": transaction.get("message"),
    }


# =========================================================
# SERIALIZATION
# =========================================================

def serialize(
    session: AgentSession,
) -> dict[str, Any]:
    """
    Convert the internal AgentSession into a frontend-safe
    JSON response.

    This intentionally exposes research information but
    avoids exposing provider/API secrets.
    """

    anakin = getattr(
        session,
        "anakin",
        None,
    )

    if anakin:
        anakin_usage = getattr(
            anakin,
            "usage",
            {
                "search_calls": 0,
                "scrape_calls": 0,
                "wire_task_calls": 0,
            },
        )

        anakin_enabled = bool(
            getattr(
                anakin,
                "enabled",
                False,
            )
        )
    else:
        anakin_usage = {
            "search_calls": 0,
            "scrape_calls": 0,
            "wire_task_calls": 0,
        }

        anakin_enabled = False

    # -----------------------------------------------------
    # Research metadata
    # -----------------------------------------------------

    evidence_coverage = getattr(
        session,
        "evidence_coverage",
        None,
    )

    candidates = getattr(
        session,
        "candidates",
        [],
    )

    evidence_gaps = getattr(
        session,
        "evidence_gaps",
        [],
    )

    sources = getattr(
        session,
        "all_snippets",
        [],
    )

    findings = getattr(
        session,
        "all_findings",
        [],
    )

    return {
        "id": session.id,

        "goal": session.goal,

        "contact_email": getattr(session, "contact_email", ""),

        "status": session.status,

        "iteration": session.iteration,

        "reasoning_log": session.reasoning_log,

        "timings": session.timings,

        "decision": session.decision,

        "action_result": session.action_result,

        "report_url": (
            "/reports/"
            + Path(session.report_path).name
            if session.report_path
            else None
        ),

        "discord_sent": session.discord_sent,

        "error": session.error,

        # -------------------------------------------------
        # Research state
        # -------------------------------------------------

        "research": {
            "findings": findings,
            "candidates": candidates,
            "evidence_gaps": evidence_gaps,
            "evidence_coverage": evidence_coverage,
            "source_count": len(sources),
            "anakin_source_count": len(
                getattr(
                    session,
                    "anakin_results",
                    [],
                )
            ),
        },

        # -------------------------------------------------
        # Anakin telemetry
        # -------------------------------------------------

        "anakin": {
            "enabled": anakin_enabled,
            "usage": anakin_usage,
            "sources": len(
                getattr(
                    session,
                    "anakin_results",
                    [],
                )
            ),
        },
    }


# =========================================================
# FULL PIPELINE
# =========================================================

async def run_full_pipeline(
    session: AgentSession,
    request: StartRequest,
) -> None:
    """
    Main Scout execution pipeline.

    Pipeline:

        PLAN
          ↓
        RESEARCH
          ↓
        EVIDENCE / VERIFICATION
          ↓
        COMPARE
          ↓
        OPTIONAL ACTION
          ↓
        REPORT
          ↓
        DISCORD
    """

    t_start = time.time()

    try:

        # =================================================
        # 1. PLAN
        # =================================================

        session.status = "planning"

        t0 = time.time()

        queries = await session.plan()

        session._log_stage(
            "plan",
            time.time() - t0,
            {
                "queries": queries,
            },
        )

        # =================================================
        # 2. RESEARCH
        # =================================================
        #
        # IMPORTANT:
        #
        # research_loop() is now responsible for:
        #
        # - understanding the research task
        # - gathering sources
        # - ranking sources
        # - extracting candidates
        # - checking evidence
        # - identifying evidence gaps
        # - targeted follow-up research
        # - Anakin enrichment
        # - building the evidence set
        #
        # main.py should NOT perform another Anakin call.
        # =================================================

        session.status = "researching"

        t0 = time.time()

        await session.research_loop(
            queries
        )

        session._log_stage(
            "research",
            time.time() - t0,
            {
                "source_count": len(
                    getattr(
                        session,
                        "all_snippets",
                        [],
                    )
                ),
                "finding_count": len(
                    getattr(
                        session,
                        "all_findings",
                        [],
                    )
                ),
                "candidate_count": len(
                    getattr(
                        session,
                        "candidates",
                        [],
                    )
                ),
                "evidence_gaps": getattr(
                    session,
                    "evidence_gaps",
                    [],
                ),
            },
        )

        # =================================================
        # 3. COMPARE
        # =================================================

        session.status = "comparing"

        t0 = time.time()

        await session.compare_options()

        session._log_stage(
            "compare",
            time.time() - t0,
            {
                "winner": (
                    session.decision.get(
                        "winner"
                    )
                    if session.decision
                    else None
                ),
            },
        )

        # =================================================
        # 4. OPTIONAL ACTION
        # =================================================

        # Research mode NEVER performs an external action.

        if request.action_mode == "research":

            session.status = "complete"

            t0 = time.time()

            session.action_result = {
                "success": False,
                "performed": False,
                "reason": (
                    "Research-only run. "
                    "No external form was submitted."
                ),
            }

            session._log_stage(
                "action",
                time.time() - t0,
                {
                    "mode": "research",
                    "performed": False,
                },
            )

        # -------------------------------------------------
        # Explicit form-fill mode
        # -------------------------------------------------

        elif request.action_mode == "form_fill":

            session.status = "acting"

            t0 = time.time()

            # Never allow an unconfirmed external action.
            if not request.confirm_action:

                session.action_result = {
                    "success": False,
                    "performed": False,
                    "reason": (
                        "Form action was requested "
                        "without explicit confirmation."
                    ),
                }

            elif not request.target_url:

                session.action_result = {
                    "success": False,
                    "performed": False,
                    "reason": (
                        "target_url is required "
                        "for form_fill mode."
                    ),
                }

            else:

                session.action_result = (
                    await asyncio.to_thread(
                        fill_and_submit_form,
                        request.target_url,
                        {
                            "name": request.name,
                            "email": request.email,
                        },
                        submit=True,
                    )
                )

            session._log_stage(
                "action",
                time.time() - t0,
                {
                    "mode": "form_fill",
                    "performed": bool(
                        session.action_result.get(
                            "performed",
                            False,
                        )
                        if isinstance(
                            session.action_result,
                            dict,
                        )
                        else False
                    ),
                },
            )

        # =================================================
        # 5. DELIVER
        # =================================================

        session.status = "delivering"

        t0 = time.time()

        # -------------------------------------------------
        # Generate PDF
        # -------------------------------------------------

        session.report_path = build_pdf(
            session
        )

        # -------------------------------------------------
        # Discord notification
        # -------------------------------------------------

        winner = (
            session.decision.get(
                "winner",
                "n/a",
            )
            if session.decision
            else "n/a"
        )

        session.discord_sent = await send_discord(
            (
                "Scout finished researching "
                f"**{session.goal}** and selected "
                f"**{winner}**."
            ),
            session.report_path,
        )

        session._log_stage(
            "deliver",
            time.time() - t0,
            {
                "report_created": bool(
                    session.report_path
                ),
                "discord_sent": bool(
                    session.discord_sent
                ),
            },
        )

        # =================================================
        # 6. TOTAL
        # =================================================

        session._log_stage(
            "total",
            time.time() - t_start,
            {
                "anakin_usage": (
                    getattr(
                        session.anakin,
                        "usage",
                        {},
                    )
                    if getattr(
                        session,
                        "anakin",
                        None,
                    )
                    else {}
                ),
                "sources": len(
                    getattr(
                        session,
                        "all_snippets",
                        [],
                    )
                ),
                "findings": len(
                    getattr(
                        session,
                        "all_findings",
                        [],
                    )
                ),
                "candidates": len(
                    getattr(
                        session,
                        "candidates",
                        [],
                    )
                ),
            },
        )

        # =================================================
        # DONE
        # =================================================

        session.status = "done"

    except Exception as exc:

        # -------------------------------------------------
        # Internal error handling
        # -------------------------------------------------

        session.status = "error"

        session.error = str(
            exc
        )

        session._log_stage(
            "error",
            time.time() - t_start,
            {
                "error": str(exc)[:1200],
            },
        )

    finally:

        # -------------------------------------------------
        # Always close provider/browser resources.
        # -------------------------------------------------

        try:
            await session.close()
        except Exception as close_exc:

            # Do not replace the original pipeline error.
            if not session.error:
                session.error = (
                    f"Cleanup error: {close_exc}"
                )


# =========================================================
# START AGENT
# =========================================================

@app.post(
    "/agent/start",
    response_model=StartResponse,
)
async def start_agent(
    req: StartRequest,
    background_tasks: BackgroundTasks,
):

    session = AgentSession(req.goal, contact_email=req.email)

    SESSIONS[
        session.id
    ] = session

    background_tasks.add_task(
        run_full_pipeline,
        session,
        req,
    )

    return StartResponse(
        session_id=session.id
    )


# =========================================================
# STATUS
# =========================================================

@app.get(
    "/agent/{session_id}"
)
async def get_status(
    session_id: str,
):

    session = SESSIONS.get(
        session_id
    )

    if not session:

        raise HTTPException(
            status_code=404,
            detail="session not found",
        )

    return serialize(
        session
    )


# =========================================================
# PAYMENTS (RAZORPAY TEST MODE ONLY)
# =========================================================

@app.post("/payments/create-order")
async def payment_create_order(req: CreateOrderRequest) -> dict[str, Any]:
    session = SESSIONS.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    option = _get_purchase_option(session, req.option_name)
    try:
        researched_currency = ensure_currency_enabled(option["currency"])
        charge_currency = settlement_currency()
        # Razorpay India can only settle in INR (unless International
        # Payments has been separately approved for the account), so a
        # researched price in USD/EUR/etc. is converted to the account's
        # settlement currency here. Netbanking/UPI also only appear in the
        # Razorpay Checkout widget for INR orders, so this is also what
        # makes net banking available for every researched option.
        charge_price, fx_rate = await convert_to_settlement_currency(
            float(option["price"]), researched_currency, charge_currency
        )
        amount = amount_in_subunits(charge_price, charge_currency)
        order = await create_order(amount=amount, currency=charge_currency, session_id=session.id, option=option)
    except PaymentError as exc:
        raise HTTPException(status_code=502 if "Razorpay" in str(exc) or "reach" in str(exc) else 400, detail=str(exc)) from exc

    transaction = {
        "session_id": session.id,
        "option_name": option["name"],
        "purchase_type": option.get("purchase_type", "unknown"),
        "provider": option.get("provider", ""),
        "transaction_details": option.get("transaction_details", []),
        "purchase_url": option["purchase_url"],
        "price": charge_price,
        "currency": charge_currency,
        "researched_price": option["price"],
        "researched_currency": researched_currency,
        "fx_rate": fx_rate,
        "amount": amount,
        "order_id": order["id"],
        "verified": False,
        "email_sent": False,
    }
    async with PAYMENT_LOCK:
        PAYMENT_TRANSACTIONS[order["id"]] = transaction
    return {
        "order_id": order["id"],
        "key_id": settings.RAZORPAY_KEY_ID,
        "amount": amount,
        "currency": transaction["currency"],
        "option_name": transaction["option_name"],
        "price": transaction["price"],
        "researched_price": transaction["researched_price"],
        "researched_currency": transaction["researched_currency"],
        "fx_rate": transaction["fx_rate"],
    }


async def _finalize_payment(transaction: dict[str, Any], payment_id: str) -> dict[str, Any]:
    """Mark a verified payment once; invoice sending is an explicit later step."""
    async with PAYMENT_LOCK:
        existing_order = PAYMENT_IDS.get(payment_id)
        if existing_order and existing_order != transaction["order_id"]:
            raise HTTPException(status_code=400, detail="payment ID was already used for another order")
        if transaction.get("verified"):
            return _transaction_response(transaction)
        transaction["verified"] = True
        transaction["payment_id"] = payment_id
        transaction["verified_at"] = datetime.now(timezone.utc).isoformat()
        PAYMENT_IDS[payment_id] = transaction["order_id"]

    transaction["message"] = "Payment verified. You can request an invoice by email."
    return _transaction_response(transaction)


@app.post("/payments/verify")
async def payment_verify(req: VerifyPaymentRequest) -> dict[str, Any]:
    session = SESSIONS.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    _get_purchase_option(session, req.option_name)
    async with PAYMENT_LOCK:
        transaction = PAYMENT_TRANSACTIONS.get(req.razorpay_order_id)
    if not transaction or transaction["session_id"] != session.id or transaction["option_name"] != req.option_name:
        raise HTTPException(status_code=400, detail="order does not match this Scout session and option")
    if not verify_checkout_signature(req.razorpay_order_id, req.razorpay_payment_id, req.razorpay_signature):
        raise HTTPException(status_code=400, detail="invalid Razorpay payment signature")
    try:
        payment, order = await retrieve_payment_and_order(req.razorpay_payment_id, req.razorpay_order_id)
        verify_remote_payment(payment=payment, order=order, expected_order_id=transaction["order_id"], expected_amount=transaction["amount"], expected_currency=transaction["currency"], session_id=session.id, option_name=transaction["option_name"])
    except PaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _finalize_payment(transaction, req.razorpay_payment_id)


@app.post("/payments/send-invoice")
async def payment_send_invoice(req: InvoiceRequest) -> dict[str, Any]:
    """Explicit, idempotent invoice delivery after a verified test payment."""
    async with PAYMENT_LOCK:
        transaction = PAYMENT_TRANSACTIONS.get(req.razorpay_order_id)
        if not transaction or transaction["session_id"] != req.session_id:
            raise HTTPException(status_code=404, detail="verified transaction not found")
        if not transaction.get("verified"):
            raise HTTPException(status_code=400, detail="payment has not been verified")
        if transaction.get("email_sent") and transaction.get("invoice_email") == req.email:
            return {**_transaction_response(transaction), "invoice_status": "already_sent"}
        transaction["invoice_email"] = req.email

    try:
        await send_demo_confirmation(req.email, transaction)
    except Exception:
        transaction["email_sent"] = False
        transaction["message"] = "Payment verified successfully, but we couldn't send the invoice email."
        return {**_transaction_response(transaction), "invoice_status": "failed"}

    transaction["email_sent"] = True
    transaction["message"] = "Payment verified and invoice email sent."
    return {**_transaction_response(transaction), "invoice_status": "sent"}


@app.post("/payments/webhook")
async def payment_webhook(request: Request) -> dict[str, bool]:
    raw_body = await request.body()
    if not verify_webhook_signature(raw_body, request.headers.get("X-Razorpay-Signature")):
        raise HTTPException(status_code=401, detail="invalid webhook signature")
    try:
        event = await request.json()
        payment = event.get("payload", {}).get("payment", {}).get("entity", {})
        order_id, payment_id = payment.get("order_id"), payment.get("id")
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="invalid webhook payload")
    if event.get("event") != "payment.captured" or not order_id or not payment_id:
        return {"ok": True}
    async with PAYMENT_LOCK:
        transaction = PAYMENT_TRANSACTIONS.get(order_id)
    if not transaction:
        return {"ok": True}
    try:
        remote_payment, order = await retrieve_payment_and_order(payment_id, order_id)
        verify_remote_payment(payment=remote_payment, order=order, expected_order_id=transaction["order_id"], expected_amount=transaction["amount"], expected_currency=transaction["currency"], session_id=transaction["session_id"], option_name=transaction["option_name"])
        await _finalize_payment(transaction, payment_id)
    except (PaymentError, HTTPException):
        # Razorpay may retry delivery; acknowledge verified events without
        # exposing internal payment details or creating duplicate fulfillment.
        return {"ok": True}
    return {"ok": True}


# =========================================================
# HEALTH
# =========================================================

@app.get(
    "/health"
)
async def health():

    return {
        "ok": True,

        "anakin_configured": bool(
            settings.ANAKIN_API_KEY
        ),

        "models": {
            "groq": settings.GROQ_MODELS,
            "openrouter": settings.OPENROUTER_MODELS,
            "google": settings.GOOGLE_MODELS,
        },
    }
