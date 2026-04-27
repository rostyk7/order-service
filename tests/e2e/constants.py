"""E2e test constants — environment config and shared test data."""

from __future__ import annotations

import os

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
E2E_TIMEOUT = int(os.getenv("E2E_TIMEOUT", "15"))
ADMIN_KEY = os.getenv("ADMIN_API_KEY", "dev-admin-key")

# tok_success rotation — coordinated across both e2e test files (shared DB session).
# Two emails keep card_distinct_emails_last_24h ≤ 2 and email_orders_last_hour ≤ 3.
_SA = "e2e-success-a@test.com"
_SB = "e2e-success-b@test.com"

# Emails for fake-card (tok_test) compliance orders only — never mixed with tok_success.
_CTA = "e2e-comp-a@test.com"
_CTB = "e2e-comp-b@test.com"

# Structuring band [900_000, 999_999] cents → compliance score 35 → FLAGGED.
STRUCTURING_AMOUNT = 950_000
