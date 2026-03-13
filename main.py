"""
SA-ID Enterprise Backend - main.py
Version 3.0.0 — Windows Test Mode
Includes: Enterprise pipelines + Citizen App pipelines
"""
import logging
import uvicorn
import time
import hashlib
import hmac
import secrets
import random
from contextlib import asynccontextmanager
from typing import Optional

import jwt
from fastapi import FastAPI, Request, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── Citizen pipelines ──────────────────────────────────────────────────────────
from citizen_auth_pipeline      import run_face_auth_pipeline, run_pin_auth_pipeline, run_document_scan_pipeline
from citizen_sign_pipeline      import run_document_sign_pipeline
from citizen_profile_pipeline   import run_dha_lookup_pipeline, run_update_details_pipeline, run_attach_id_pipeline
from citizen_payments_pipeline  import run_grant_status_pipeline, run_payment_history_pipeline
from citizen_notification_pipeline import run_notification_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SA-ID] %(levelname)s - %(message)s",
)
log = logging.getLogger("said.main")

JWT_SECRET = "test-jwt-secret-for-windows-testing-only"
JWT_ALGO   = "HS256"
HMAC_KEY   = b"test-hmac-secret-32bytes-minimum!"
TEST_API_KEY = "test-api-key-windows-001"


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("=" * 60)
    log.info("SA-ID Enterprise Platform v3.0.0 starting")
    log.info("Pipelines: Enterprise + Citizen App (8 pipelines total)")
    log.info("Mode: Windows Test (GPU on Jetson AGX Orin)")
    log.info("=" * 60)
    yield
    log.info("SA-ID shutdown complete")


app = FastAPI(
    title="SA-ID Enterprise + Citizen Platform",
    description="""
## SA-ID Platform v3.0.0

### Enterprise Pipelines (Banks, Government, Retail)
- Identity verification with biometrics
- SARB ISO 8583 payments
- DHA + SASSA API integration
- Cloud audit trail

### Citizen App Pipelines
- Face recognition login
- PIN verification
- Document (SA-ID card) scan
- Document signing (Legal, Medical, Corporate, Govt)
- DHA record lookup
- Grant status & payment history
- Contact detail updates
- SA-ID card attachment
- SMS/Email/Push notifications
""",
    version="3.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def verify_sa_id_checksum(id_number: str) -> bool:
    if len(id_number) != 13 or not id_number.isdigit():
        return False
    total = 0
    for i, d in enumerate(id_number[:-1]):
        n = int(d)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return (10 - (total % 10)) % 10 == int(id_number[-1])

def check_auth(authorization: Optional[str]):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")


# ══════════════════════════════════════════════════════════════════════════════
# HEALTH
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/health/ping", tags=["Health"])
async def ping():
    return {"status": "ok", "timestamp": int(time.time())}


@app.get("/api/v1/health/ready", tags=["Health"])
async def ready():
    return {
        "status":          "ready",
        "version":         "3.0.0",
        "mode":            "windows-test",
        "jax_backend":     "cpu (GPU on Jetson)",
        "enterprise_pipelines": ["mrz", "dha", "sarb", "sassa", "cloud_sync"],
        "citizen_pipelines":    ["auth_face", "auth_pin", "auth_doc", "sign", "profile", "grant", "payments", "notify"],
        "timestamp":       int(time.time()),
    }


# ══════════════════════════════════════════════════════════════════════════════
# AUTHENTICATION (Enterprise)
# ══════════════════════════════════════════════════════════════════════════════

class TokenRequest(BaseModel):
    terminal_id: str
    merchant_id: str
    client_type: str
    api_key:     str

@app.post("/api/v1/auth/token", tags=["Authentication"])
async def get_token(req: TokenRequest):
    ALLOWED = {"BANK", "GOVERNMENT", "TAX_OFFICE", "CORPORATION", "COMPANY", "CITIZEN_APP"}
    if req.client_type not in ALLOWED:
        return JSONResponse(status_code=403, content={"error": "Unauthorised client type"})
    if len(req.api_key) < 16:
        return JSONResponse(status_code=401, content={"error": "API key too short"})
    payload = {
        "sub":         req.terminal_id,
        "client_type": req.client_type,
        "merchant_id": req.merchant_id,
        "exp":         int(time.time()) + 1800,
        "jti":         secrets.token_hex(16),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)
    return {"access_token": token, "token_type": "bearer",
            "expires_in": 1800, "terminal_id": req.terminal_id}


# ══════════════════════════════════════════════════════════════════════════════
# CITIZEN APP — AUTH PIPELINES
# ══════════════════════════════════════════════════════════════════════════════

class FaceAuthRequest(BaseModel):
    id_number:       str
    face_data_b64:   Optional[str] = None   # base64 face image (on Jetson: real frame)

@app.post("/api/v1/citizen/auth/face", tags=["Citizen — Auth"])
async def citizen_face_auth(req: FaceAuthRequest,
                             authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    result = run_face_auth_pipeline(req.id_number, req.face_data_b64)
    if not result["success"]:
        return JSONResponse(status_code=400, content=result)
    return result


class PinAuthRequest(BaseModel):
    id_number: str
    pin:       str   # 6-digit PIN — never logged

@app.post("/api/v1/citizen/auth/pin", tags=["Citizen — Auth"])
async def citizen_pin_auth(req: PinAuthRequest,
                            authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    result = run_pin_auth_pipeline(req.id_number, req.pin)
    if not result["success"]:
        return JSONResponse(status_code=400, content=result)
    return result


class DocScanRequest(BaseModel):
    id_number:     str
    mrz_data:      Optional[str] = None   # MRZ text from barcode scanner
    card_image_b64: Optional[str] = None

@app.post("/api/v1/citizen/auth/document", tags=["Citizen — Auth"])
async def citizen_doc_auth(req: DocScanRequest,
                            authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    result = run_document_scan_pipeline(req.id_number, req.mrz_data)
    if not result["success"]:
        return JSONResponse(status_code=400, content=result)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# CITIZEN APP — DOCUMENT SIGNING
# ══════════════════════════════════════════════════════════════════════════════

class SignRequest(BaseModel):
    id_number:          str
    document_type:      str           # legal / medical / corporate / govt
    requesting_party:   str
    reference_number:   Optional[str] = None
    signature_data_b64: Optional[str] = None
    document_content:   Optional[str] = None

@app.post("/api/v1/citizen/document/sign", tags=["Citizen — Document Signing"])
async def citizen_sign_document(req: SignRequest,
                                 authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    result = run_document_sign_pipeline(
        req.id_number, req.document_type, req.requesting_party,
        req.reference_number, req.signature_data_b64, req.document_content
    )
    if not result["success"]:
        return JSONResponse(status_code=400, content=result)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# CITIZEN APP — PROFILE PIPELINES
# ══════════════════════════════════════════════════════════════════════════════

class IDLookupRequest(BaseModel):
    id_number: str

@app.post("/api/v1/citizen/profile/dha", tags=["Citizen — Profile"])
async def citizen_dha_lookup(req: IDLookupRequest,
                              authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    result = run_dha_lookup_pipeline(req.id_number)
    if not result["success"]:
        return JSONResponse(status_code=400, content=result)
    return result


class UpdateDetailsRequest(BaseModel):
    id_number: str
    mobile:    Optional[str] = None
    email:     Optional[str] = None
    address:   Optional[str] = None

@app.post("/api/v1/citizen/profile/update", tags=["Citizen — Profile"])
async def citizen_update_details(req: UpdateDetailsRequest,
                                  authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    result = run_update_details_pipeline(req.id_number, req.mobile, req.email, req.address)
    if not result["success"]:
        return JSONResponse(status_code=400, content=result)
    return result


class AttachIDRequest(BaseModel):
    id_number:       str
    card_image_b64:  Optional[str] = None
    card_type:       str = "smart_id"    # smart_id or green_id

@app.post("/api/v1/citizen/profile/attach-id", tags=["Citizen — Profile"])
async def citizen_attach_id(req: AttachIDRequest,
                             authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    result = run_attach_id_pipeline(req.id_number, req.card_image_b64, req.card_type)
    if not result["success"]:
        return JSONResponse(status_code=400, content=result)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# CITIZEN APP — GRANT & PAYMENTS
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/v1/citizen/grant/status", tags=["Citizen — Grants & Payments"])
async def citizen_grant_status(req: IDLookupRequest,
                                authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    result = run_grant_status_pipeline(req.id_number)
    if not result["success"]:
        return JSONResponse(status_code=400, content=result)
    return result


class PaymentHistoryRequest(BaseModel):
    id_number: str
    months:    int = 6

@app.post("/api/v1/citizen/grant/payments", tags=["Citizen — Grants & Payments"])
async def citizen_payment_history(req: PaymentHistoryRequest,
                                   authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    result = run_payment_history_pipeline(req.id_number, req.months)
    if not result["success"]:
        return JSONResponse(status_code=400, content=result)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# CITIZEN APP — NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════════════════

class NotificationRequest(BaseModel):
    id_number_hash:    str
    notification_type: str
    channel:           str           # sms / email / push
    recipient:         str
    reference:         Optional[str] = None
    custom_message:    Optional[str] = None

@app.post("/api/v1/citizen/notify", tags=["Citizen — Notifications"])
async def citizen_notify(req: NotificationRequest,
                          authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    result = run_notification_pipeline(
        req.id_number_hash, req.notification_type,
        req.channel, req.recipient, req.reference, req.custom_message
    )
    if not result["success"]:
        return JSONResponse(status_code=400, content=result)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# ENTERPRISE — IDENTITY VERIFICATION (unchanged from v2.1.0)
# ══════════════════════════════════════════════════════════════════════════════

class VerifyRequest(BaseModel):
    id_number:      str
    surname:        str
    given_names:    str
    dob:            str
    terminal_id:    str
    live_frame_b64: Optional[str] = None
    doc_frame_b64:  Optional[str] = None

@app.post("/api/v1/identity/verify", tags=["Enterprise — Identity"])
async def verify_identity(req: VerifyRequest,
                          authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    t0 = time.perf_counter()
    checksum_ok = verify_sa_id_checksum(req.id_number)
    if not checksum_ok:
        return {
            "verified": False, "result": "REJECT",
            "reject_reason": "Document checksum invalid",
            "id_hash": hashlib.sha256(req.id_number.encode()).hexdigest()[:16],
            "total_ms": round((time.perf_counter() - t0) * 1000, 1),
            "timestamp": int(time.time()),
        }
    bio_score = round(random.uniform(0.83, 0.97), 4)
    live_conf = round(random.uniform(0.92, 0.99), 4)
    total_ms  = round((time.perf_counter() - t0) * 1000, 1)
    ts        = int(time.time())
    tx_hash   = hmac.new(
        HMAC_KEY,
        f"{req.id_number}{bio_score}{ts}PASS".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "verified": True, "result": "PASS", "reject_reason": None,
        "id_hash": hashlib.sha256(req.id_number.encode()).hexdigest()[:16],
        "bio_score": bio_score, "liveness": "REAL", "liveness_conf": live_conf,
        "dha_verified": True, "total_ms": total_ms,
        "timestamp": ts, "tx_hash": tx_hash, "version": "3.0.0",
    }


@app.post("/api/v1/identity/checksum", tags=["Enterprise — Identity"])
async def checksum_only(id_number: str,
                        authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    return {
        "id_number_valid": verify_sa_id_checksum(id_number),
        "id_length_ok":    len(id_number) == 13,
        "timestamp":       int(time.time()),
    }


# ══════════════════════════════════════════════════════════════════════════════
# ENTERPRISE — PAYMENTS & AUDIT
# ══════════════════════════════════════════════════════════════════════════════

class PayRequest(BaseModel):
    amount_zar:  float
    method:      str
    merchant_id: str
    terminal_id: str
    id_number:   str
    id_verified: bool = False

@app.post("/api/v1/payment/initiate", tags=["Enterprise — Payments"])
async def initiate_payment(req: PayRequest,
                           authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    if req.amount_zar >= 5000.0 and not req.id_verified:
        raise HTTPException(status_code=403,
            detail="Transactions >= R5,000 ZAR require SA-ID biometric verification")
    tx_id = f"SAID-TX-{secrets.token_hex(8).upper()}"
    return {"tx_id": tx_id, "status": "pending", "amount_zar": req.amount_zar,
            "method": req.method, "timestamp": int(time.time())}


@app.get("/api/v1/audit/verify-chain", tags=["Enterprise — Audit"])
async def verify_chain(authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    return {"valid": True, "entries": 0,
            "head_hash": "GENESIS", "timestamp": int(time.time())}


# ══════════════════════════════════════════════════════════════════════════════
# ERROR HANDLER
# ══════════════════════════════════════════════════════════════════════════════

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error("Error: %s | %s", exc, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "code": "SAID_500"},
    )


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8001, log_level="info")
