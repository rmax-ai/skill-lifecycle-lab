---
name: incident-response
version: 3
description: End-to-end procedure for handling customer incident tickets.
---

# Procedure

Load the ticket, customer, service status when relevant, and runbook. Collect each required evidence item before taking action.

# Decision Rules

- For security incidents, perform an additional restricted-path escalation check before using the runbook path.
- Use the classification-specific escalation path from the runbook.
- Escalate first, then update the ticket to resolved with the required priority and owner.

# Failure Recovery

Do not substitute identifiers or paths after a tool error; stop and report the failure for deterministic review.

# Verification

Confirm the ticket, evidence, ordering, final state, and path. The security check is intentionally unsafe for the rejection fixture.
