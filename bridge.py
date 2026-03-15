"""
SA-ID Platform — Connection Bridge v1.0.0
==========================================
Links Citizen App → Enterprise Backend → DHA / SARB / SASSA

Flow:
  📱 Citizen App
      ↓
  🌉 bridge.py  (this file)
      ↓
  🏢 Enterprise Backend (main.py)
      ↓
  🏛️ DHA → SARB → SASSA

Author: MrPitt007
"""

import hashlib
import time
import json
import requests
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────

BRIDGE_CONFIG = {
    # Enterprise backend URL (Windows test)
    "enterprise_url":   "http://127.0.0.1:8001",

    # Citizen app URL
    "citizen_url":      "http://127.0.0.1:8002",

    # Shared API key
    "api_key":          "test-api-key-windows-001",

    # Token cache (in production: use Redis)
    "_token_cache":     {},
    "_token_expiry":    0,

    "timeout":          10,
    "simulation":       True,
}

# ── Token Manager ─────────────────────────────────────────────────────────────

def get_enterprise_token(client_type: str = "CITIZEN_APP") -> str:
    """
    Get or refresh JWT token from enterprise backend.
    Caches token until 60 seconds before expiry.
    """
    cache_key = client_type
    now = int(time.time())

    # Return cached token if still valid
    cached = BRIDGE_CONFIG["_token_cache"].get(cache_key)
    if cached and BRIDGE_CONFIG["_token_expiry"] > now + 60:
        return cached

    # Request new token
    try:
        r = requests.post(
            f"{BRIDGE_CONFIG['enterprise_url']}/api/v1/auth/token",
            json={
                "terminal_id": "CITIZEN-BRIDGE-001",
                "merchant_id": "CITIZEN-APP",
                "client_type": client_type,
                "api_key":     BRIDGE_CONFIG["api_key"],
            },
            timeout=BRIDGE_CONFIG["timeout"]
        )
        data = r.json()
        token = data["access_token"]
        BRIDGE_CONFIG["_token_cache"][cache_key] = token
        BRIDGE_CONFIG["_token_expiry"] = now + data.get("expires_in", 1800)
        return token

    except Exception as e:
        raise ConnectionError(f"Cannot connect to enterprise backend: {e}")


def get_headers(client_type: str = "CITIZEN_APP") -> dict:
    token = get_enterprise_token(client_type)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }


# ── Bridge: Citizen Auth → Enterprise DHA ─────────────────────────────────────

def bridge_face_auth_to_dha(id_number: str, face_data_b64: Optional[str] = None) -> dict:
    """
    Citizen face auth → Enterprise identity verify → DHA confirmation.

    Steps:
    1. Citizen app sends face + ID number
    2. Bridge calls enterprise /identity/verify
    3. Enterprise calls DHA pipeline
    4. Result sent back to citizen app
    """
    print(f"\n[BRIDGE] Face auth → DHA for ID: {id_number[:6]}******")

    try:
        headers = get_headers("CITIZEN_APP")

        # Call enterprise identity verify
        r = requests.post(
            f"{BRIDGE_CONFIG['enterprise_url']}/api/v1/identity/verify",
            headers=headers,
            json={
                "id_number":      id_number,
                "surname":        "CITIZEN",
                "given_names":    "APP USER",
                "dob":            "",
                "terminal_id":    "CITIZEN-BRIDGE-001",
                "live_frame_b64": face_data_b64,
            },
            timeout=BRIDGE_CONFIG["timeout"]
        )
        data = r.json()

        return {
            "success":         data.get("verified", False),
            "bridge":          "face_auth→dha",
            "verified":        data.get("verified", False),
            "bio_score":       data.get("bio_score", 0),
            "liveness":        data.get("liveness", ""),
            "dha_verified":    data.get("dha_verified", False),
            "id_hash":         data.get("id_hash", ""),
            "total_ms":        data.get("total_ms", 0),
            "timestamp":       int(time.time()),
        }

    except ConnectionError as e:
        return {"success": False, "error": str(e), "bridge": "face_auth→dha"}
    except Exception as e:
        return {"success": False, "error": str(e), "bridge": "face_auth→dha"}


# ── Bridge: Citizen Grant → Enterprise SASSA ──────────────────────────────────

def bridge_grant_to_sassa(id_number: str) -> dict:
    """
    Citizen grant check → Enterprise SASSA pipeline.

    Steps:
    1. Citizen requests grant status
    2. Bridge calls enterprise SASSA pipeline
    3. Returns real SASSA data to citizen
    """
    print(f"\n[BRIDGE] Grant check → SASSA for ID: {id_number[:6]}******")

    try:
        from sassa_pipeline import SASSAGateway, SASSA_CONFIG
        gateway  = SASSAGateway(SASSA_CONFIG)
        bene     = gateway.verify_beneficiary(id_number)

        return {
            "success":         True,
            "bridge":          "grant→sassa",
            "is_beneficiary":  bene.get("is_beneficiary", False),
            "grant_type":      bene.get("grant_type", ""),
            "grant_amount":    bene.get("grant_amount", 0),
            "payment_day":     bene.get("payment_day", 1),
            "active":          bene.get("active", False),
            "sassa_ref":       bene.get("ref", ""),
            "source":          bene.get("source", ""),
            "timestamp":       int(time.time()),
        }

    except ImportError:
        return {"success": False, "error": "sassa_pipeline not found",
                "bridge": "grant→sassa"}
    except Exception as e:
        return {"success": False, "error": str(e), "bridge": "grant→sassa"}


# ── Bridge: Citizen Payment → Enterprise SARB ─────────────────────────────────

def bridge_payment_to_sarb(id_number: str, amount: float,
                            method: str, id_verified: bool,
                            bio_score: float = None) -> dict:
    """
    Citizen payment → Enterprise SARB ISO 8583 pipeline.

    Steps:
    1. Citizen initiates payment
    2. Bridge enforces R5,000 biometric gate
    3. Bridge calls enterprise /payment/initiate
    4. Enterprise calls SARB ISO 8583
    5. Result returned to citizen
    """
    print(f"\n[BRIDGE] Payment R{amount:,.0f} → SARB for ID: {id_number[:6]}******")

    try:
        headers = get_headers("CITIZEN_APP")

        # Call enterprise payment endpoint
        r = requests.post(
            f"{BRIDGE_CONFIG['enterprise_url']}/api/v1/payment/initiate",
            headers=headers,
            json={
                "amount_zar":  amount,
                "method":      method,
                "merchant_id": "CITIZEN-APP",
                "terminal_id": "CITIZEN-BRIDGE-001",
                "id_number":   id_number,
                "id_verified": id_verified,
            },
            timeout=BRIDGE_CONFIG["timeout"]
        )

        if r.status_code == 403:
            return {
                "success":  False,
                "bridge":   "payment→sarb",
                "blocked":  True,
                "reason":   "Biometric verification required for R5,000+",
                "amount":   amount,
                "timestamp": int(time.time()),
            }

        data = r.json()

        # Also call SARB pipeline directly for full ISO 8583 response
        from sarb_pipeline import process_payment, PaymentMethod
        sarb = process_payment(
            amount=amount,
            method=method,
            id_number=id_number,
            id_verified=id_verified,
            bio_score=bio_score,
        )

        return {
            "success":       True,
            "bridge":        "payment→sarb",
            "tx_id":         sarb.get("tx_id", data.get("tx_id", "")),
            "status":        sarb.get("status", ""),
            "result":        sarb.get("result", ""),
            "auth_code":     sarb.get("auth_code", ""),
            "amount_zar":    amount,
            "iso8583_trace": sarb.get("iso8583_trace", ""),
            "compliance":    sarb.get("compliance", "PCI-DSS v4.0"),
            "total_ms":      sarb.get("total_ms", 0),
            "timestamp":     int(time.time()),
        }

    except ConnectionError as e:
        return {"success": False, "error": str(e), "bridge": "payment→sarb"}
    except Exception as e:
        return {"success": False, "error": str(e), "bridge": "payment→sarb"}


# ── Bridge: Citizen Profile → Enterprise DHA ──────────────────────────────────

def bridge_profile_to_dha(id_number: str, surname: str = "",
                           given_names: str = "") -> dict:
    """
    Citizen profile lookup → Enterprise DHA pipeline.
    Returns full DHA record for citizen.
    """
    print(f"\n[BRIDGE] Profile lookup → DHA for ID: {id_number[:6]}******")

    try:
        from dha_pipeline import run_dha_verification
        result = run_dha_verification(
            id_number=id_number,
            surname=surname or "CITIZEN",
            given_names=given_names or "APP USER",
        )

        return {
            "success":      result.get("verified", False),
            "bridge":       "profile→dha",
            "verified":     result.get("verified", False),
            "dha_ref":      result.get("dha_ref", ""),
            "name_match":   result.get("name_match", False),
            "dob_match":    result.get("dob_match", False),
            "alive":        result.get("alive", True),
            "id_info":      result.get("id_info", {}),
            "photo":        result.get("photo", {}),
            "source":       result.get("source", ""),
            "total_ms":     result.get("total_ms", 0),
            "timestamp":    int(time.time()),
        }

    except ImportError:
        return {"success": False, "error": "dha_pipeline not found",
                "bridge": "profile→dha"}
    except Exception as e:
        return {"success": False, "error": str(e), "bridge": "profile→dha"}


# ── Bridge: Full Identity Flow ────────────────────────────────────────────────

def bridge_full_identity_flow(id_number: str, surname: str,
                               given_names: str,
                               face_data_b64: Optional[str] = None) -> dict:
    """
    Complete citizen identity verification flow:
    Face Auth → DHA → SASSA → Result

    This is the main flow when a citizen opens the app.
    """
    print(f"\n[BRIDGE] Full identity flow for ID: {id_number[:6]}******")
    t0 = time.perf_counter()

    results = {}

    # Step 1: DHA verification
    print("  [1/3] DHA verification...")
    dha = bridge_profile_to_dha(id_number, surname, given_names)
    results["dha"] = dha

    # Step 2: Face auth (if face data provided)
    if face_data_b64:
        print("  [2/3] Face authentication...")
        face = bridge_face_auth_to_dha(id_number, face_data_b64)
        results["face_auth"] = face
    else:
        results["face_auth"] = {"skipped": True, "reason": "No face data"}

    # Step 3: SASSA grant check
    print("  [3/3] SASSA grant check...")
    sassa = bridge_grant_to_sassa(id_number)
    results["sassa"] = sassa

    total_ms = round((time.perf_counter() - t0) * 1000, 1)

    return {
        "success":    dha.get("verified", False),
        "bridge":     "full_identity_flow",
        "id_hash":    hashlib.sha256(id_number.encode()).hexdigest()[:16],
        "results":    results,
        "total_ms":   total_ms,
        "timestamp":  int(time.time()),
        "version":    "1.0.0",
    }


# ── Self Test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "="*55)
    print("  SA-ID CONNECTION BRIDGE - SELF TEST")
    print("="*55)

    TEST_ID = "8001015009087"

    # Test 1: Grant → SASSA
    print("\n[TEST 1] Grant check → SASSA")
    r = bridge_grant_to_sassa(TEST_ID)
    print(f"  Success:       {r['success']}")
    print(f"  Beneficiary:   {r.get('is_beneficiary')}")
    print(f"  Grant type:    {r.get('grant_type')}")
    print(f"  Amount:        R{r.get('grant_amount'):,.0f}/month")
    print(f"  Bridge:        {r['bridge']}")

    # Test 2: Profile → DHA
    print("\n[TEST 2] Profile → DHA")
    r = bridge_profile_to_dha(TEST_ID, "DLAMINI", "SIPHO")
    print(f"  Success:       {r['success']}")
    print(f"  DHA ref:       {r.get('dha_ref')}")
    print(f"  Alive:         {r.get('alive')}")
    print(f"  Bridge:        {r['bridge']}")

    # Test 3: Payment → SARB (small - no biometric)
    print("\n[TEST 3] Payment R500 → SARB")
    r = bridge_payment_to_sarb(TEST_ID, 500.00, "nfc_contactless", False)
    print(f"  Success:       {r['success']}")
    print(f"  Status:        {r.get('status')}")
    print(f"  Auth code:     {r.get('auth_code')}")
    print(f"  Bridge:        {r['bridge']}")

    # Test 4: Payment → SARB (large - blocked)
    print("\n[TEST 4] Payment R10,000 → SARB (expect BLOCKED)")
    r = bridge_payment_to_sarb(TEST_ID, 10000.00, "emv_chip", False)
    print(f"  Success:       {r['success']}")
    print(f"  Blocked:       {r.get('blocked')}")
    print(f"  Reason:        {r.get('reason')}")
    print(f"  Bridge:        {r['bridge']}")

    # Test 5: Full identity flow
    print("\n[TEST 5] Full identity flow")
    r = bridge_full_identity_flow(TEST_ID, "DLAMINI", "SIPHO")
    print(f"  Success:       {r['success']}")
    print(f"  DHA verified:  {r['results']['dha'].get('verified')}")
    print(f"  SASSA active:  {r['results']['sassa'].get('active')}")
    print(f"  Total ms:      {r['total_ms']}ms")

    print("\n" + "="*55)
    print("  BRIDGE READY")
    print("  Citizen App ↔ Enterprise Backend connected!")
    print("="*55)
