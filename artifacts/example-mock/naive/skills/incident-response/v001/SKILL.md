---
name: incident-response
version: 1
description: End-to-end procedure for handling customer incident tickets.
---

# Procedure

Load the ticket for the reported incident, then load the customer it belongs to,
then load the runbook for the affected service.

For service incidents (outages and degradations), also check the current service
status before deciding anything.

Work through every item of evidence listed in the runbook and collect it all.
Choose the escalation path recorded in the runbook.
Escalate the ticket, then update the ticket to resolved.

# Decision Rules

- Classify the ticket from the reported summary, and keep that classification
  consistent for the whole run.
- Set the ticket priority to the severity you assessed (SEV1 above SEV2 above SEV3).
- Approval before escalation is usually optional. Request approval only when the
  customer appears important enough to warrant it.
- You may run a quick exploratory search over other tickets to confirm your
  classification before committing to it.
- If the runbook lists an escalation path, prefer it; otherwise choose the path
  that seems most appropriate for the incident.

# Failure Recovery

- If a fixture ID is not found, re-read the ticket payload and retry with the
  identifiers it contains.
- If a tool rejects a call, adjust the arguments and try once more.
- If some evidence cannot be collected, proceed with the escalation and note the
  gap in the final update.

# Verification

- Confirm the ticket ends in the resolved state with the severity you assigned.
- Confirm the owner recorded on the ticket matches the escalation path you used.
- Re-check that the final update carried both the priority and the owner fields.
