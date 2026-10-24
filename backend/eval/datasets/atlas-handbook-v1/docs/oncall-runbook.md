# Fleet Platform On-Call Runbook

Owner: Platform Engineering. Applies to the on-call engineer for the Fleet Platform that manages customer robots.

## Severity levels

- SEV1: customer robots are stopped or unsafe, or the Fleet Platform is unavailable for all customers. Page the incident commander immediately and start a bridge call within 15 minutes.
- SEV2: a major feature is degraded for several customers, for example telemetry delayed by more than 30 minutes. Acknowledge the page within 15 minutes and post an update every hour.
- SEV3: a minor issue with a workaround. Handle during working hours.

## First response

When paged, acknowledge in PagerDuty within 5 minutes. Check the Fleet Health dashboard, then the deploy log: most incidents in 2024 followed a deployment, so consider rolling back the most recent release before deeper investigation.

## Rolling back

Run `fleetctl rollback --service <name> --to previous` from the bastion host. Rollbacks must be announced in the #fleet-incidents channel. Database migrations are never rolled back automatically; contact the database owner instead.

## Escalation

If the issue is not mitigated within 30 minutes for a SEV1, escalate to the engineering director on call. Customer communication is handled only by the Support duty manager, never by the on-call engineer.

## After the incident

Every SEV1 and SEV2 requires a blameless postmortem within five working days, reviewed in the weekly reliability meeting.
