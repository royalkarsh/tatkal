"""Single use action warrants for UDIR payee responses (105 / 106), with the signer and the
verifier separated.

  PolicySigner   lives with the policy gate (agent side). Holds an Ed25519 PRIVATE key. Signs a
                 warrant only after re-checking that both readers returned the label the code
                 requires.
  WarrantVerifier lives in the rail adapter. Holds only the PUBLIC key, so it can check a
                 signature but can never create one. It checks again, on its own, that the
                 warrant's readers agree with the code, and that the warrant is bound to this
                 complaint, code, transaction and amount, unexpired and unspent.

Why: v3.2 used one HMAC key reachable from the rail object, so any caller could mint a valid
warrant (external review, 30 Oct). Pattern from GateArena (Jiang, Quinzan, Bhatt, NeurIPS 2026
workshop "Agents in the Wild", openreview.net/forum?id=2fckzsPCbK): enforce in the handler and
bind the confirmation to the exact parameters. Commit time re-check from Santos Grueiro,
arXiv 2607.10487.

Honest scope: in this prototype both objects live in one Python process, so this is an
in-process policy control. In production the private key would sit in a separate policy
service or HSM and the rail adapter would only ever receive the public key.
"""
from __future__ import annotations

import json
import os
import secrets
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

TTL_SECONDS = 120
REQUIRED_LABEL = {"105": "goods_delivered_yes", "106": "refund_yes"}
FIELDS = ("wid", "complaint_id", "code", "txn_id", "amount_inr", "readers", "override", "issued_at", "generation")


class WarrantError(ValueError):
    pass


def _body(w: dict) -> bytes:
    return json.dumps({k: w.get(k) for k in FIELDS}, sort_keys=True, ensure_ascii=False).encode()


def readers_agree(code: str, readers: dict | None) -> bool:
    need = REQUIRED_LABEL.get(code)
    r = readers or {}
    return bool(need) and r.get("rule") == need and r.get("sarvam") == need


class PolicySigner:
    def __init__(self, seed_hex: str | None = None, ttl: int = TTL_SECONDS):
        seed = seed_hex or os.getenv("TATKAL_POLICY_SEED")
        self._key = (Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed)) if seed
                     else Ed25519PrivateKey.generate())
        self.ttl = ttl

    @property
    def public_key(self) -> bytes:
        return self._key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    def issue(self, *, complaint_id: str, code: str, txn_id: str, amount_inr: int, readers: dict,
              single_key_override: bool = False, generation: str | None = None) -> dict:
        """Signs only if the readers agree with the code (or the labelled demo override is on)."""
        if code not in REQUIRED_LABEL:
            raise WarrantError(f"{code} is not a payee resolve code")
        override = None
        if not readers_agree(code, readers):
            if single_key_override and readers.get("rule") == REQUIRED_LABEL[code]:
                override = "single_key_demo"
            else:
                raise WarrantError("Policy refuses to sign: the two readers do not both say "
                                   f"{REQUIRED_LABEL[code]}")
        w = {"wid": secrets.token_hex(8), "complaint_id": complaint_id, "code": code, "txn_id": txn_id,
             "amount_inr": amount_inr, "readers": dict(readers), "override": override,
             "issued_at": int(time.time()), "generation": generation}
        return self._sign(w)

    def _sign(self, w: dict) -> dict:
        """Raw signing without checks. Only used by tests to prove the rail re-checks on its own."""
        return {**w, "sig": self._key.sign(_body(w)).hex()}


class WarrantVerifier:
    def __init__(self, public_key: bytes, ttl: int = TTL_SECONDS):
        self._pub = Ed25519PublicKey.from_public_bytes(public_key)
        self.ttl = ttl
        self.spent: set[str] = set()

    def spend(self, w: dict | None, *, complaint_id: str, code: str, txn_id: str, amount_inr: int,
              allow_single_key: bool = False, generation: str | None = None) -> str:
        """Verify and consume. Raises WarrantError with the exact reason on any mismatch."""
        if not w:
            raise WarrantError("No warrant: 105/106 needs a warrant signed by the policy gate")
        try:
            self._pub.verify(bytes.fromhex(w["sig"]), _body(w))
        except (KeyError, TypeError, ValueError, InvalidSignature):
            raise WarrantError("Warrant signature invalid: not signed by the policy gate") from None
        if w["wid"] in self.spent:
            raise WarrantError("Warrant already used")
        if (w.get("complaint_id"), w.get("code"), w.get("txn_id"), w.get("amount_inr")) != (complaint_id, code, txn_id, amount_inr):
            raise WarrantError("Warrant was issued for a different complaint, code or amount")
        if generation is not None and w.get("generation") != generation:
            raise WarrantError("Warrant belongs to an earlier rail session (reset since it was issued)")
        if time.time() - w.get("issued_at", 0) > self.ttl:
            raise WarrantError("Warrant expired; ask the shopkeeper again")
        if not readers_agree(code, w.get("readers")):
            if not (allow_single_key and w.get("override") == "single_key_demo"):
                raise WarrantError("Rail refuses: the warrant's two readers do not agree with " + code)
        self.spent.add(w["wid"])
        return w["wid"]
