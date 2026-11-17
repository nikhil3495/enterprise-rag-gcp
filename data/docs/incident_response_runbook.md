# Incident Response and On-Call Runbook

Owner: Site Reliability Engineering · Last reviewed: August 2026. This runbook covers production
incidents affecting the Helios Cloud fleet platform, customer-facing APIs and robots in the field.

## 1. Severity Levels

Severity is assigned by the first responder and may be changed by the Incident Commander.

### 1.1 SEV1 — Critical

A complete outage of the fleet platform, a safety-impacting robot fault, data loss, or a security breach
affecting customers. **Acknowledge within 15 minutes**, 24/7. An Incident Commander (IC) must be
appointed immediately. Status updates go to stakeholders **every 30 minutes**. The VP of Engineering and
customer success leadership are notified within 1 hour.

### 1.2 SEV2 — Major

Significant degradation of a core feature for many customers (for example, fleet dashboards unavailable
or mission scheduling delayed by more than 10 minutes) without a safety impact. **Acknowledge within
30 minutes**, 24/7. Status updates every 60 minutes.

### 1.3 SEV3 — Minor

Partial degradation with a workaround, or an issue affecting a single customer. Acknowledge within
**4 business hours**. Handled during business hours by the owning team.

### 1.4 SEV4 — Low

Cosmetic issues, documentation errors, or requests. Acknowledge by the **next business day** and track in
the team backlog.

## 2. On-Call Rotation

### 2.1 Schedule

Each service team runs a weekly on-call rotation with a **primary** and a **secondary** engineer.
Handoff happens every **Monday at 10:00** local time, with a 15-minute handoff review of open incidents
and alerts. Engineers are on call at most one week in four. On-call engineers receive a stipend of
**$300 per week** on call, plus time off in lieu for any page handled between 22:00 and 07:00.

### 2.2 Paging and escalation

Alerts page the primary. If the primary does not acknowledge within **10 minutes**, the page escalates
automatically to the secondary; after 20 minutes without acknowledgement it escalates to the engineering
manager. The on-call engineer must have a laptop and network access and be able to respond within the
SEV1 acknowledgement window at all times.

## 3. Running an Incident

1. **Acknowledge** the page and open an incident channel named `#inc-<date>-<short-name>`.
2. **Assess** impact and assign a severity using section 1.
3. **Appoint roles** for SEV1 and SEV2: Incident Commander, Communications Lead, Operations Lead.
4. **Mitigate first**: roll back, fail over, or disable the feature flag before root-causing.
5. **Communicate** on the cadence for the severity; customer-facing updates go via the status page.
6. **Resolve** when customer impact has ended, and record the timeline in the incident document.

For robot safety faults, the Operations Lead can trigger a **fleet-wide safe stop** from the fleet portal;
this requires a second engineer's confirmation.

## 4. Postmortems

A blameless postmortem is **required for every SEV1 and SEV2** incident, and optional for SEV3.
The postmortem draft must be published within **5 business days** of resolution and reviewed in the weekly
reliability review. Every action item must have an owner and a due date; SEV1 action items are due within
30 days. Postmortems include: summary, impact, timeline, root cause, what went well, what went poorly,
and action items.

## 5. Tooling

Paging uses the on-call platform; dashboards are in the observability stack; runbooks for individual
alerts are linked from each alert. Practice drills ("game days") are run quarterly for each service.
