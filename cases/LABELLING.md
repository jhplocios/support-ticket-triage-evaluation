* Enum Definitions
  - category:
    * billing: Invoices, card charges, refunds, subscription downgrades/upgrades.
    * bug: Crashes, 500 errors, UI glitches, broken links, unexpected behavior.
    * account_access: 2FA issues, password resets, locked accounts, SSO failures.
    * feature_request: Suggestions, missing capabilities, UI preference changes.
    * other: Job applications, partnership inquiries, general feedback, spam.
  - priority:
    * urgent: System outage, active revenue loss, inaccessible production account.
    * normal: Standard workflow blocked or general inquiry requiring a response.
    * low: Cosmetic feedback, non-blocking feature suggestions, general commentary.

* Order ID Specification
  - Regex: ^ORD-\d{5}$ (Strict uppercase ORD- followed by exactly 5 digits).
  - Whitespace trimming: Strip leading and trailing whitespace.
  - Invalid IDs: Any deviation (e.g., INV-12345, ord-12345, ORD-123) must label as null.

* Tie-Break Rules
  - Multi-Issue Hierarchy: Financial/Billing takes precedence over Technical Glitches (billing > bug > account_access > feature_request > other).
  - Sentiment vs. Severity: Emotional rants or all-caps shouting do not elevate priority. Priority is dictated solely by functional and operational impact.

* Human Escalation (needs_human)
  - Triggers true if:
    * There is a billing dispute or refund request lacking a valid order_id.
    * Legal threats, chargeback warnings, or account deactivation disputes occur.
  - Remains false for standard automated triage, simple resets, or routine feature requests.