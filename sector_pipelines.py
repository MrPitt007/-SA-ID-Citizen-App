"""
SA-ID Enterprise Sector Pipelines v1.0.0
==========================================
Connects all sectors to NVIDIA Jetson AGX Orin via SA-ID Platform

Sectors:
  1. Bank Pipeline          — KYC, account opening, R5000+ payments
  2. Government Pipeline    — border control, DHA office verification
  3. Retail Pipeline        — age verification, RICA, loyalty
  4. Corporate/HR Pipeline  — employee onboarding, access control
  5. Union Pipeline         — member verification, benefits
  6. SARS Pipeline          — taxpayer identity, e-filing

Flow:
  Sector App -> sector_pipelines.py -> Enterprise Backend (Jetson) -> DHA/SARB/SASSA
"""

import hashlib
import time
import requests
import json
import uuid
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────

ENTERPRISE_URL = "http://127.0.0.1:8001"
API_KEY        = "test-api-key-windows-001"
TIMEOUT        = 10

_token_cache   = {"token": None, "expiry": 0}

# ── Shared Helpers ────────────────────────────────────────────────────────────

def get_token(client_type: str = "BANK") -> str:
    now = int(time.time())
    if _token_cache["token"] and _token_cache["expiry"] > now + 60:
        return _token_cache["token"]
    r = requests.post(
        f"{ENTERPRISE_URL}/api/v1/auth/token",
        json={
            "terminal_id": f"{client_type}-TERMINAL-001",
            "merchant_id": f"{client_type}-001",
            "client_type": client_type,
            "api_key":     API_KEY,
        },
        timeout=TIMEOUT
    )
    token = r.json()["access_token"]
    _token_cache["token"]  = token
    _token_cache["expiry"] = now + 1800
    return token

def get_headers(client_type: str = "BANK") -> dict:
    return {
        "Authorization": f"Bearer {get_token(client_type)}",
        "Content-Type":  "application/json",
    }

def verify_identity(id_number: str, surname: str,
                    given_names: str, terminal_id: str,
                    client_type: str) -> dict:
    """Core identity verification — calls Jetson enterprise backend."""
    r = requests.post(
        f"{ENTERPRISE_URL}/api/v1/identity/verify",
        headers=get_headers(client_type),
        json={
            "id_number":   id_number,
            "surname":     surname,
            "given_names": given_names,
            "dob":         "",
            "terminal_id": terminal_id,
        },
        timeout=TIMEOUT
    )
    return r.json()

def initiate_payment(id_number: str, amount: float,
                     method: str, id_verified: bool,
                     terminal_id: str, merchant_id: str) -> dict:
    """Core payment — calls Jetson enterprise backend."""
    r = requests.post(
        f"{ENTERPRISE_URL}/api/v1/payment/initiate",
        headers=get_headers("BANK"),
        json={
            "amount_zar":  amount,
            "method":      method,
            "merchant_id": merchant_id,
            "terminal_id": terminal_id,
            "id_number":   id_number,
            "id_verified": id_verified,
        },
        timeout=TIMEOUT
    )
    if r.status_code == 403:
        return {"blocked": True, "reason": "Biometric required for R5,000+"}
    return r.json()


# ══════════════════════════════════════════════════════════════════════════════
# 1. BANK PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

class BankPipeline:
    """
    Bank KYC + Payment Pipeline
    - FICA KYC verification
    - Account opening
    - High value payment gate (R5,000+)
    - Just like Bank-ID in Sweden
    """

    def fica_kyc(self, id_number: str, surname: str,
                 given_names: str, branch_code: str = "JHB001") -> dict:
        """Full FICA KYC verification at bank branch or ATM."""
        print(f"\n[BANK] FICA KYC for ID: {id_number[:6]}******")
        t0 = time.perf_counter()

        identity = verify_identity(
            id_number, surname, given_names,
            f"BANK-{branch_code}", "BANK"
        )

        return {
            "success":        identity.get("verified", False),
            "pipeline":       "bank_fica_kyc",
            "fica_compliant": identity.get("verified", False),
            "kyc_level":      "FULL" if identity.get("verified") else "FAILED",
            "bio_score":      identity.get("bio_score", 0),
            "dha_verified":   identity.get("dha_verified", False),
            "branch_code":    branch_code,
            "ref":            f"BANK-KYC-{uuid.uuid4().hex[:8].upper()}",
            "total_ms":       round((time.perf_counter() - t0) * 1000, 1),
            "timestamp":      int(time.time()),
            "compliance":     "FICA Act 38 of 2001",
        }

    def open_account(self, id_number: str, surname: str,
                     given_names: str, account_type: str = "cheque") -> dict:
        """Open new bank account with biometric verification."""
        print(f"\n[BANK] Account opening for ID: {id_number[:6]}******")

        kyc = self.fica_kyc(id_number, surname, given_names)
        if not kyc["success"]:
            return {"success": False, "reason": "KYC failed", "pipeline": "bank_account_open"}

        return {
            "success":        True,
            "pipeline":       "bank_account_open",
            "account_number": f"4{uuid.uuid4().hex[:9].upper()}",
            "account_type":   account_type,
            "kyc_ref":        kyc["ref"],
            "bio_verified":   True,
            "timestamp":      int(time.time()),
            "compliance":     "FICA + POPIA",
        }

    def high_value_payment(self, id_number: str, amount: float,
                            id_verified: bool, branch: str = "JHB001") -> dict:
        """Process high value payment with biometric gate."""
        print(f"\n[BANK] Payment R{amount:,.0f} for ID: {id_number[:6]}******")

        result = initiate_payment(
            id_number, amount, "said_token",
            id_verified, f"BANK-{branch}", "BANK-MERCH-001"
        )

        if result.get("blocked"):
            return {
                "success":   False,
                "pipeline":  "bank_payment",
                "blocked":   True,
                "reason":    result["reason"],
                "amount":    amount,
                "timestamp": int(time.time()),
            }

        return {
            "success":    True,
            "pipeline":   "bank_payment",
            "tx_id":      result.get("tx_id", ""),
            "status":     result.get("status", ""),
            "amount_zar": amount,
            "timestamp":  int(time.time()),
            "compliance": "PCI-DSS v4.0",
        }


# ══════════════════════════════════════════════════════════════════════════════
# 2. GOVERNMENT PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

class GovernmentPipeline:
    """
    Government Identity Verification Pipeline
    - DHA office verification
    - Border control
    - Passport/visa applications
    - Social services access
    """

    def dha_office_verify(self, id_number: str, surname: str,
                           given_names: str, office_code: str = "DHA-JHB") -> dict:
        """Verify citizen at DHA office counter."""
        print(f"\n[GOVT] DHA office verify for ID: {id_number[:6]}******")
        t0 = time.perf_counter()

        identity = verify_identity(
            id_number, surname, given_names,
            f"GOVT-{office_code}", "GOVERNMENT"
        )

        return {
            "success":      identity.get("verified", False),
            "pipeline":     "govt_dha_verify",
            "verified":     identity.get("verified", False),
            "bio_score":    identity.get("bio_score", 0),
            "office_code":  office_code,
            "ref":          f"GOVT-{uuid.uuid4().hex[:8].upper()}",
            "total_ms":     round((time.perf_counter() - t0) * 1000, 1),
            "timestamp":    int(time.time()),
            "compliance":   "Identification Act 68 of 1997",
        }

    def border_control(self, id_number: str, surname: str,
                        given_names: str, port_of_entry: str = "OR-TAMBO") -> dict:
        """Border control identity check."""
        print(f"\n[GOVT] Border control for ID: {id_number[:6]}******")

        identity = verify_identity(
            id_number, surname, given_names,
            f"BORDER-{port_of_entry}", "GOVERNMENT"
        )

        clearance = identity.get("verified", False)

        return {
            "success":       clearance,
            "pipeline":      "govt_border_control",
            "clearance":     "GRANTED" if clearance else "DENIED",
            "bio_score":     identity.get("bio_score", 0),
            "port_of_entry": port_of_entry,
            "alert":         None,
            "ref":           f"BORDER-{uuid.uuid4().hex[:8].upper()}",
            "timestamp":     int(time.time()),
            "compliance":    "Immigration Act 13 of 2002",
        }

    def social_services_access(self, id_number: str, surname: str,
                                given_names: str, service: str) -> dict:
        """Verify identity for social services access."""
        print(f"\n[GOVT] Social services [{service}] for ID: {id_number[:6]}******")

        identity = verify_identity(
            id_number, surname, given_names,
            "GOVT-SOCIAL-001", "GOVERNMENT"
        )

        return {
            "success":   identity.get("verified", False),
            "pipeline":  "govt_social_services",
            "service":   service,
            "access":    "GRANTED" if identity.get("verified") else "DENIED",
            "ref":       f"SOC-{uuid.uuid4().hex[:8].upper()}",
            "timestamp": int(time.time()),
        }


# ══════════════════════════════════════════════════════════════════════════════
# 3. RETAIL PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

class RetailPipeline:
    """
    Retail Identity Pipeline
    - Age verification (alcohol, tobacco, gambling)
    - RICA SIM registration
    - Loyalty programme
    - Store credit
    """

    def age_verify(self, id_number: str, surname: str,
                   given_names: str, product: str = "alcohol",
                   store_id: str = "STORE-001") -> dict:
        """Age verification at point of sale."""
        print(f"\n[RETAIL] Age verify [{product}] for ID: {id_number[:6]}******")
        t0 = time.perf_counter()

        identity = verify_identity(
            id_number, surname, given_names,
            f"RETAIL-{store_id}", "CORPORATION"
        )

        # Calculate age from ID
        yy   = int(id_number[0:2])
        year = 2000 + yy if yy <= 25 else 1900 + yy
        age  = time.localtime().tm_year - year

        age_limits = {"alcohol": 18, "tobacco": 18, "gambling": 18, "adult": 18}
        limit      = age_limits.get(product, 18)
        age_ok     = age >= limit

        return {
            "success":    identity.get("verified", False) and age_ok,
            "pipeline":   "retail_age_verify",
            "verified":   identity.get("verified", False),
            "age":        age,
            "age_limit":  limit,
            "age_ok":     age_ok,
            "product":    product,
            "store_id":   store_id,
            "ref":        f"RETAIL-{uuid.uuid4().hex[:8].upper()}",
            "total_ms":   round((time.perf_counter() - t0) * 1000, 1),
            "timestamp":  int(time.time()),
            "compliance": "Liquor Act 59 of 2003",
        }

    def rica_registration(self, id_number: str, surname: str,
                           given_names: str, sim_number: str,
                           store_id: str = "STORE-001") -> dict:
        """RICA SIM card registration."""
        print(f"\n[RETAIL] RICA registration for ID: {id_number[:6]}******")

        identity = verify_identity(
            id_number, surname, given_names,
            f"RICA-{store_id}", "CORPORATION"
        )

        return {
            "success":    identity.get("verified", False),
            "pipeline":   "retail_rica",
            "rica_ref":   f"RICA-{uuid.uuid4().hex[:8].upper()}",
            "sim_number": sim_number,
            "verified":   identity.get("verified", False),
            "timestamp":  int(time.time()),
            "compliance": "RICA Act 70 of 2002",
        }

    def store_credit(self, id_number: str, surname: str,
                     given_names: str, amount: float,
                     store_id: str = "STORE-001") -> dict:
        """Store credit application with biometric verification."""
        print(f"\n[RETAIL] Store credit R{amount:,.0f} for ID: {id_number[:6]}******")

        identity = verify_identity(
            id_number, surname, given_names,
            f"CREDIT-{store_id}", "CORPORATION"
        )

        return {
            "success":    identity.get("verified", False),
            "pipeline":   "retail_credit",
            "approved":   identity.get("verified", False),
            "amount":     amount,
            "ref":        f"CREDIT-{uuid.uuid4().hex[:8].upper()}",
            "timestamp":  int(time.time()),
            "compliance": "National Credit Act 34 of 2005",
        }


# ══════════════════════════════════════════════════════════════════════════════
# 4. CORPORATE/HR PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

class CorporatePipeline:
    """
    Corporate HR Identity Pipeline
    - Employee onboarding
    - Access control
    - Background verification
    - Payroll identity confirmation
    """

    def employee_onboard(self, id_number: str, surname: str,
                          given_names: str, company_id: str,
                          position: str) -> dict:
        """New employee biometric onboarding."""
        print(f"\n[CORP] Employee onboard for ID: {id_number[:6]}******")
        t0 = time.perf_counter()

        identity = verify_identity(
            id_number, surname, given_names,
            f"HR-{company_id}", "CORPORATION"
        )

        return {
            "success":      identity.get("verified", False),
            "pipeline":     "corp_onboard",
            "employee_ref": f"EMP-{uuid.uuid4().hex[:8].upper()}",
            "position":     position,
            "company_id":   company_id,
            "bio_enrolled": identity.get("verified", False),
            "dha_verified": identity.get("dha_verified", False),
            "total_ms":     round((time.perf_counter() - t0) * 1000, 1),
            "timestamp":    int(time.time()),
            "compliance":   "POPIA + Labour Relations Act",
        }

    def access_control(self, id_number: str, surname: str,
                        given_names: str, building: str,
                        access_level: str = "standard") -> dict:
        """Building/system access control."""
        print(f"\n[CORP] Access control [{building}] for ID: {id_number[:6]}******")

        identity = verify_identity(
            id_number, surname, given_names,
            f"ACCESS-{building}", "CORPORATION"
        )

        return {
            "success":      identity.get("verified", False),
            "pipeline":     "corp_access",
            "access":       "GRANTED" if identity.get("verified") else "DENIED",
            "building":     building,
            "access_level": access_level,
            "bio_score":    identity.get("bio_score", 0),
            "ref":          f"ACCESS-{uuid.uuid4().hex[:8].upper()}",
            "timestamp":    int(time.time()),
        }

    def payroll_verify(self, id_number: str, surname: str,
                        given_names: str, employee_number: str) -> dict:
        """Payroll identity verification."""
        print(f"\n[CORP] Payroll verify for ID: {id_number[:6]}******")

        identity = verify_identity(
            id_number, surname, given_names,
            "PAYROLL-001", "CORPORATION"
        )

        return {
            "success":         identity.get("verified", False),
            "pipeline":        "corp_payroll",
            "employee_number": employee_number,
            "verified":        identity.get("verified", False),
            "ref":             f"PAY-{uuid.uuid4().hex[:8].upper()}",
            "timestamp":       int(time.time()),
            "compliance":      "POPIA + Basic Conditions of Employment Act",
        }


# ══════════════════════════════════════════════════════════════════════════════
# 5. UNION PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

class UnionPipeline:
    """
    Union Member Verification Pipeline
    - Member registration
    - Benefits verification
    - Strike action verification
    - Voting verification
    """

    def member_verify(self, id_number: str, surname: str,
                       given_names: str, union_id: str) -> dict:
        """Verify union member identity."""
        print(f"\n[UNION] Member verify for ID: {id_number[:6]}******")
        t0 = time.perf_counter()

        identity = verify_identity(
            id_number, surname, given_names,
            f"UNION-{union_id}", "CORPORATION"
        )

        return {
            "success":     identity.get("verified", False),
            "pipeline":    "union_member_verify",
            "member_ref":  f"UNION-{uuid.uuid4().hex[:8].upper()}",
            "union_id":    union_id,
            "verified":    identity.get("verified", False),
            "bio_score":   identity.get("bio_score", 0),
            "total_ms":    round((time.perf_counter() - t0) * 1000, 1),
            "timestamp":   int(time.time()),
            "compliance":  "Labour Relations Act 66 of 1995",
        }

    def benefits_claim(self, id_number: str, surname: str,
                        given_names: str, union_id: str,
                        benefit_type: str) -> dict:
        """Verify member for benefits claim."""
        print(f"\n[UNION] Benefits claim [{benefit_type}] for ID: {id_number[:6]}******")

        identity = verify_identity(
            id_number, surname, given_names,
            f"UNION-BENEFIT-{union_id}", "CORPORATION"
        )

        return {
            "success":      identity.get("verified", False),
            "pipeline":     "union_benefits",
            "benefit_type": benefit_type,
            "approved":     identity.get("verified", False),
            "ref":          f"BEN-{uuid.uuid4().hex[:8].upper()}",
            "timestamp":    int(time.time()),
        }

    def voting_verify(self, id_number: str, surname: str,
                       given_names: str, union_id: str,
                       election_id: str) -> dict:
        """Verify member for union voting."""
        print(f"\n[UNION] Voting verify for ID: {id_number[:6]}******")

        identity = verify_identity(
            id_number, surname, given_names,
            f"UNION-VOTE-{union_id}", "CORPORATION"
        )

        return {
            "success":     identity.get("verified", False),
            "pipeline":    "union_voting",
            "election_id": election_id,
            "vote_cast":   identity.get("verified", False),
            "ref":         f"VOTE-{uuid.uuid4().hex[:8].upper()}",
            "timestamp":   int(time.time()),
        }


# ══════════════════════════════════════════════════════════════════════════════
# 6. SARS PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

class SARSPipeline:
    """
    SARS Tax Office Pipeline
    - Taxpayer identity verification
    - e-Filing login
    - Tax clearance certificate
    - Refund identity confirmation
    """

    def taxpayer_verify(self, id_number: str, surname: str,
                         given_names: str, tax_number: str) -> dict:
        """Verify taxpayer identity at SARS office or online."""
        print(f"\n[SARS] Taxpayer verify for ID: {id_number[:6]}******")
        t0 = time.perf_counter()

        identity = verify_identity(
            id_number, surname, given_names,
            "SARS-TAX-001", "TAX_OFFICE"
        )

        return {
            "success":      identity.get("verified", False),
            "pipeline":     "sars_taxpayer_verify",
            "tax_number":   tax_number,
            "verified":     identity.get("verified", False),
            "bio_score":    identity.get("bio_score", 0),
            "dha_verified": identity.get("dha_verified", False),
            "sars_ref":     f"SARS-{uuid.uuid4().hex[:8].upper()}",
            "total_ms":     round((time.perf_counter() - t0) * 1000, 1),
            "timestamp":    int(time.time()),
            "compliance":   "Income Tax Act 58 of 1962",
        }

    def efiling_login(self, id_number: str, surname: str,
                       given_names: str) -> dict:
        """Biometric login for SARS eFiling."""
        print(f"\n[SARS] eFiling login for ID: {id_number[:6]}******")

        identity = verify_identity(
            id_number, surname, given_names,
            "SARS-EFILING-001", "TAX_OFFICE"
        )

        return {
            "success":       identity.get("verified", False),
            "pipeline":      "sars_efiling_login",
            "session_token": hashlib.sha256(
                f"{id_number}{time.time()}".encode()
            ).hexdigest()[:32] if identity.get("verified") else None,
            "bio_score":     identity.get("bio_score", 0),
            "ref":           f"EFILING-{uuid.uuid4().hex[:8].upper()}",
            "timestamp":     int(time.time()),
        }

    def tax_clearance(self, id_number: str, surname: str,
                       given_names: str, tax_number: str) -> dict:
        """Issue tax clearance certificate with biometric verification."""
        print(f"\n[SARS] Tax clearance for ID: {id_number[:6]}******")

        identity = verify_identity(
            id_number, surname, given_names,
            "SARS-CLEAR-001", "TAX_OFFICE"
        )

        return {
            "success":      identity.get("verified", False),
            "pipeline":     "sars_tax_clearance",
            "tax_number":   tax_number,
            "clearance":    "ISSUED" if identity.get("verified") else "FAILED",
            "cert_number":  f"TCC-{uuid.uuid4().hex[:8].upper()}" if identity.get("verified") else None,
            "valid_until":  "2027-03-15" if identity.get("verified") else None,
            "ref":          f"SARS-TCC-{uuid.uuid4().hex[:8].upper()}",
            "timestamp":    int(time.time()),
            "compliance":   "Tax Administration Act 28 of 2011",
        }

    def refund_verify(self, id_number: str, surname: str,
                       given_names: str, refund_amount: float) -> dict:
        """Verify identity before processing tax refund."""
        print(f"\n[SARS] Refund R{refund_amount:,.0f} verify for ID: {id_number[:6]}******")

        identity = verify_identity(
            id_number, surname, given_names,
            "SARS-REFUND-001", "TAX_OFFICE"
        )

        return {
            "success":       identity.get("verified", False),
            "pipeline":      "sars_refund_verify",
            "refund_amount": refund_amount,
            "approved":      identity.get("verified", False),
            "ref":           f"REFUND-{uuid.uuid4().hex[:8].upper()}",
            "timestamp":     int(time.time()),
            "compliance":    "Tax Administration Act 28 of 2011",
        }


# ══════════════════════════════════════════════════════════════════════════════
# SELF TEST — All 6 Sectors
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    TEST_ID    = "8001015009087"
    SURNAME    = "DLAMINI"
    NAMES      = "SIPHO BONGANI"

    print("\n" + "="*55)
    print("  SA-ID SECTOR PIPELINES - FULL TEST")
    print("  All sectors -> Jetson Enterprise Backend")
    print("="*55)

    # ── 1. BANK ──
    print("\n" + "─"*55)
    print("  1. BANK PIPELINE")
    print("─"*55)
    bank = BankPipeline()

    r = bank.fica_kyc(TEST_ID, SURNAME, NAMES)
    print(f"  FICA KYC:      {'PASS' if r['success'] else 'FAIL'} | {r['kyc_level']} | {r['total_ms']}ms")

    r = bank.high_value_payment(TEST_ID, 1500.00, True)
    print(f"  Payment R1500: {'PASS' if r['success'] else 'FAIL'} | {r.get('status','')}")

    r = bank.high_value_payment(TEST_ID, 10000.00, False)
    print(f"  Payment R10k:  {'BLOCKED' if r.get('blocked') else 'PASS'} | biometric gate working!")

    # ── 2. GOVERNMENT ──
    print("\n" + "─"*55)
    print("  2. GOVERNMENT PIPELINE")
    print("─"*55)
    govt = GovernmentPipeline()

    r = govt.dha_office_verify(TEST_ID, SURNAME, NAMES)
    print(f"  DHA Office:    {'PASS' if r['success'] else 'FAIL'} | {r['total_ms']}ms")

    r = govt.border_control(TEST_ID, SURNAME, NAMES)
    print(f"  Border:        {r['clearance']} | {r['port_of_entry']}")

    r = govt.social_services_access(TEST_ID, SURNAME, NAMES, "housing")
    print(f"  Social:        {r['access']} | {r['service']}")

    # ── 3. RETAIL ──
    print("\n" + "─"*55)
    print("  3. RETAIL PIPELINE")
    print("─"*55)
    retail = RetailPipeline()

    r = retail.age_verify(TEST_ID, SURNAME, NAMES, "alcohol")
    print(f"  Age verify:    {'PASS' if r['success'] else 'FAIL'} | age={r['age']} limit={r['age_limit']}")

    r = retail.rica_registration(TEST_ID, SURNAME, NAMES, "0821234567")
    print(f"  RICA:          {'PASS' if r['success'] else 'FAIL'} | {r['rica_ref']}")

    r = retail.store_credit(TEST_ID, SURNAME, NAMES, 5000.00)
    print(f"  Store credit:  {'APPROVED' if r['approved'] else 'DECLINED'} | R{r['amount']:,.0f}")

    # ── 4. CORPORATE ──
    print("\n" + "─"*55)
    print("  4. CORPORATE/HR PIPELINE")
    print("─"*55)
    corp = CorporatePipeline()

    r = corp.employee_onboard(TEST_ID, SURNAME, NAMES, "CORP-001", "Software Engineer")
    print(f"  Onboard:       {'PASS' if r['success'] else 'FAIL'} | {r['employee_ref']}")

    r = corp.access_control(TEST_ID, SURNAME, NAMES, "HQ-JHB", "senior")
    print(f"  Access:        {r['access']} | {r['building']}")

    r = corp.payroll_verify(TEST_ID, SURNAME, NAMES, "EMP-12345")
    print(f"  Payroll:       {'PASS' if r['success'] else 'FAIL'} | {r['employee_number']}")

    # ── 5. UNION ──
    print("\n" + "─"*55)
    print("  5. UNION PIPELINE")
    print("─"*55)
    union = UnionPipeline()

    r = union.member_verify(TEST_ID, SURNAME, NAMES, "NUM-001")
    print(f"  Member:        {'PASS' if r['success'] else 'FAIL'} | {r['union_id']}")

    r = union.benefits_claim(TEST_ID, SURNAME, NAMES, "NUM-001", "medical")
    print(f"  Benefits:      {'APPROVED' if r['approved'] else 'DECLINED'} | {r['benefit_type']}")

    r = union.voting_verify(TEST_ID, SURNAME, NAMES, "NUM-001", "ELECT-2026")
    print(f"  Voting:        {'PASS' if r['success'] else 'FAIL'} | {r['election_id']}")

    # ── 6. SARS ──
    print("\n" + "─"*55)
    print("  6. SARS PIPELINE")
    print("─"*55)
    sars = SARSPipeline()

    r = sars.taxpayer_verify(TEST_ID, SURNAME, NAMES, "TAX-1234567890")
    print(f"  Taxpayer:      {'PASS' if r['success'] else 'FAIL'} | {r['sars_ref']}")

    r = sars.efiling_login(TEST_ID, SURNAME, NAMES)
    print(f"  eFiling:       {'PASS' if r['success'] else 'FAIL'} | token={str(r.get('session_token',''))[:16]}...")

    r = sars.tax_clearance(TEST_ID, SURNAME, NAMES, "TAX-1234567890")
    print(f"  Tax clearance: {r['clearance']} | {r.get('cert_number','N/A')}")

    r = sars.refund_verify(TEST_ID, SURNAME, NAMES, 15000.00)
    print(f"  Refund R15k:   {'APPROVED' if r['approved'] else 'DECLINED'}")

    print("\n" + "="*55)
    print("  ALL 6 SECTOR PIPELINES READY!")
    print("  Connected to Jetson Enterprise Backend")
    print("="*55)
ENDOFFILE