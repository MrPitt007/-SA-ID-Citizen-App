
"""
SA-ID Platform - Connection Bridge v1.0.0
Citizen App -> Enterprise Backend -> DHA / SARB / SASSA
"""
import hashlib
import time
import requests
from typing import Optional

BRIDGE_CONFIG = {
    "enterprise_url": "http://127.0.0.1:8001",
    "api_key": "test-api-key-windows-001",
    "_token_cache": {},
    "_token_expiry": 0,
    "timeout": 10,
}

def get_enterprise_token():
    now = int(time.time())
    cached = BRIDGE_CONFIG["_token_cache"].get("token")
    if cached and BRIDGE_CONFIG["_token_expiry"] > now + 60:
        return cached
    r = requests.post(
        f"{BRIDGE_CONFIG['enterprise_url']}/api/v1/auth/token",
        json={
            "terminal_id": "BRIDGE-001",
            "merchant_id": "CITIZEN-APP",
            "client_type": "CITIZEN_APP",
            "api_key": BRIDGE_CONFIG["api_key"]
        },
        timeout=10
    )
    token = r.json()["access_token"]
    BRIDGE_CONFIG["_token_cache"]["token"] = token
    BRIDGE_CONFIG["_token_expiry"] = now + 1800
    return token

def get_headers():
    return {
        "Authorization": f"Bearer {get_enterprise_token()}",
        "Content-Type": "application/json"
    }

def bridge_grant_to_sassa(id_number):
    print(f"\n[BRIDGE] Grant check -> SASSA for ID: {id_number[:6]}******")
    try:
        from sassa_pipeline import SASSAGateway, SASSA_CONFIG
        gateway = SASSAGateway(SASSA_CONFIG)
        bene = gateway.verify_beneficiary(id_number)
        return {
            "success": True,
            "bridge": "grant->sassa",
            "is_beneficiary": bene.get("is_beneficiary", False),
            "grant_type": str(bene.get("grant_type", "N/A")),
            "grant_amount": float(bene.get("grant_amount", 0)),
            "payment_day": bene.get("payment_day", 1),
            "active": bene.get("active", False),
            "sassa_ref": bene.get("ref", ""),
            "source": bene.get("source", ""),
        }
    except ImportError:
        return {"success": False, "error": "sassa_pipeline not found", "bridge": "grant->sassa", "grant_amount": 0}
    except Exception as e:
        return {"success": False, "error": str(e), "bridge": "grant->sassa", "grant_amount": 0}

def bridge_profile_to_dha(id_number, surname="", given_names=""):
    print(f"\n[BRIDGE] Profile lookup -> DHA for ID: {id_number[:6]}******")
    try:
        from dha_pipeline import run_dha_verification
        result = run_dha_verification(
            id_number=id_number,
            surname=surname or "CITIZEN",
            given_names=given_names or "APP USER"
        )
        return {
            "success": result.get("verified", False),
            "bridge": "profile->dha",
            "verified": result.get("verified", False),
            "dha_ref": result.get("dha_ref", "N/A"),
            "name_match": result.get("name_match", False),
            "dob_match": result.get("dob_match", False),
            "alive": result.get("alive", True),
            "source": result.get("source", ""),
            "total_ms": result.get("total_ms", 0),
        }
    except ImportError:
        return {"success": False, "error": "dha_pipeline not found", "bridge": "profile->dha"}
    except Exception as e:
        return {"success": False, "error": str(e), "bridge": "profile->dha"}

def bridge_payment_to_sarb(id_number, amount, method, id_verified, bio_score=None):
    print(f"\n[BRIDGE] Payment R{amount:,.0f} -> SARB for ID: {id_number[:6]}******")
    try:
        from sarb_pipeline import process_payment
        sarb = process_payment(
            amount=amount,
            method=method,
            id_number=id_number,
            id_verified=id_verified,
            bio_score=bio_score
        )
        return {
            "success": sarb.get("result") == "APPROVED",
            "bridge": "payment->sarb",
            "status": str(sarb.get("status", "")),
            "result": sarb.get("result", ""),
            "auth_code": sarb.get("auth_code", "N/A"),
            "tx_id": sarb.get("tx_id", ""),
            "amount_zar": amount,
            "reason": sarb.get("reason", ""),
            "total_ms": sarb.get("total_ms", 0),
        }
    except ImportError:
        return {"success": False, "error": "sarb_pipeline not found", "bridge": "payment->sarb"}
    except Exception as e:
        return {"success": False, "error": str(e), "bridge": "payment->sarb"}

def bridge_face_auth_to_dha(id_number, face_data_b64=None):
    print(f"\n[BRIDGE] Face auth -> DHA for ID: {id_number[:6]}******")
    try:
        headers = get_headers()
        r = requests.post(
            f"{BRIDGE_CONFIG['enterprise_url']}/api/v1/identity/verify",
            headers=headers,
            json={
                "id_number": id_number,
                "surname": "CITIZEN",
                "given_names": "APP USER",
                "dob": "",
                "terminal_id": "CITIZEN-BRIDGE-001",
                "live_frame_b64": face_data_b64,
            },
            timeout=BRIDGE_CONFIG["timeout"]
        )
        data = r.json()
        return {
            "success": data.get("verified", False),
            "bridge": "face_auth->dha",
            "verified": data.get("verified", False),
            "bio_score": data.get("bio_score", 0),
            "liveness": data.get("liveness", ""),
            "dha_verified": data.get("dha_verified", False),
            "total_ms": data.get("total_ms", 0),
        }
    except Exception as e:
        return {"success": False, "error": str(e), "bridge": "face_auth->dha"}

def bridge_full_identity_flow(id_number, surname, given_names):
    print(f"\n[BRIDGE] Full identity flow for ID: {id_number[:6]}******")
    t0 = time.perf_counter()
    dha = bridge_profile_to_dha(id_number, surname, given_names)
    sassa = bridge_grant_to_sassa(id_number)
    total_ms = round((time.perf_counter() - t0) * 1000, 1)
    return {
        "success": dha.get("verified", False),
        "bridge": "full_identity_flow",
        "id_hash": hashlib.sha256(id_number.encode()).hexdigest()[:16],
        "dha": dha,
        "sassa": sassa,
        "total_ms": total_ms,
        "timestamp": int(time.time()),
    }

if __name__ == "__main__":
    TEST_ID = "8001015009087"

    print("\n" + "="*55)
    print("  SA-ID CONNECTION BRIDGE - SELF TEST")
    print("="*55)

    print("\n[TEST 1] Grant check -> SASSA")
    r = bridge_grant_to_sassa(TEST_ID)
    print(f"  Success:      {r['success']}")
    print(f"  Beneficiary:  {r.get('is_beneficiary')}")
    print(f"  Grant type:   {r.get('grant_type')}")
    print(f"  Amount:       R{r.get('grant_amount', 0):,.0f}/month")
    print(f"  Active:       {r.get('active')}")

    print("\n[TEST 2] Profile -> DHA")
    r = bridge_profile_to_dha(TEST_ID, "DLAMINI", "SIPHO")
    print(f"  Success:      {r['success']}")
    print(f"  Verified:     {r.get('verified')}")
    print(f"  DHA ref:      {r.get('dha_ref')}")
    print(f"  Alive:        {r.get('alive')}")

    print("\n[TEST 3] Payment R500 -> SARB (no biometric)")
    r = bridge_payment_to_sarb(TEST_ID, 500.00, "nfc_contactless", False)
    print(f"  Success:      {r['success']}")
    print(f"  Status:       {r.get('status')}")
    print(f"  Auth code:    {r.get('auth_code')}")
    print(f"  Total ms:     {r.get('total_ms')}ms")

    print("\n[TEST 4] Payment R10,000 -> SARB (expect BLOCKED)")
    r = bridge_payment_to_sarb(TEST_ID, 10000.00, "emv_chip", False)
    print(f"  Success:      {r['success']}")
    print(f"  Result:       {r.get('result')}")
    print(f"  Reason:       {r.get('reason')}")

    print("\n[TEST 5] Full identity flow")
    r = bridge_full_identity_flow(TEST_ID, "DLAMINI", "SIPHO")
    print(f"  Success:      {r['success']}")
    print(f"  DHA verified: {r['dha'].get('verified')}")
    print(f"  SASSA active: {r['sassa'].get('active')}")
    print(f"  Total ms:     {r['total_ms']}ms")

    print("\n" + "="*55)
    print("  BRIDGE READY - Citizen App connected!")
    print("="*55)

       
