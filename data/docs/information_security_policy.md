# Helios Robotics Information Security Policy

Policy ID: SEC-001 · Version 4.0 · Approved by the CISO on February 3, 2026 · Review cycle: annual.
This policy applies to all employees, contractors and third parties who access Helios Robotics systems,
networks, robot fleets or data.

## 1. Identity and Authentication

### 1.1 Multi-factor authentication

Multi-factor authentication (MFA) is **mandatory for every account** that accesses company systems,
including email, source control, cloud consoles, the fleet management portal and VPN. SMS one-time codes
are not permitted as a second factor. Administrator and other privileged accounts must use
**phishing-resistant FIDO2 hardware security keys**; two keys are issued per administrator, one kept as a
backup in a secure location.

### 1.2 Passwords

Passwords must be at least **14 characters** long. Passphrases are encouraged. Passwords must be stored in
the company-approved password manager and must never be reused across systems. Helios does **not** enforce
periodic password expiry; a password must be changed immediately if compromise is suspected.
Accounts lock for 15 minutes after **10 consecutive failed sign-in attempts**.

### 1.3 Access management

Access follows least privilege and is granted through role-based groups. Managers review their team's
access **quarterly**; access to Restricted data is reviewed by the data owner every quarter as well.
Accounts of departing employees are disabled within **4 hours** of the end of employment.

## 2. Keys, Secrets and Certificates

### 2.1 Rotation schedule

* API keys, service-account credentials and other application secrets must be rotated at least every
  **90 days**, and immediately on suspected exposure.
* Customer-managed encryption keys (KMS) are rotated automatically **every 365 days**.
* TLS certificates are issued by the internal CA or a public ACME provider and auto-renew 30 days before
  expiry; certificates may not be valid for more than 398 days.
* SSH keys for production access are short-lived certificates valid for 12 hours.

### 2.2 Secret storage

Secrets must be stored in the managed secret manager. Secrets must never be committed to source control,
pasted into tickets or chat, or embedded in container images. Automated secret scanning runs on every
pull request and blocks merges on detection.

## 3. Data Classification

All Helios data is classified into one of four levels. The data owner assigns the classification.

| Level | Description | Examples |
| --- | --- | --- |
| Public | Approved for public release | Marketing site, published datasheets |
| Internal | Default for business information | Wiki pages, internal roadmaps, org charts |
| Confidential | Business-sensitive information | Customer contracts, pricing, unreleased firmware |
| Restricted | Highest sensitivity; legal or safety impact if disclosed | Customer PII, robot telemetry tied to a customer, firmware signing keys, pre-release financial results |

### 3.1 Handling requirements

Confidential and Restricted data must be encrypted in transit (TLS 1.2 or higher) and at rest.
Restricted data may only be stored in approved systems listed in the data inventory, may not be copied to
removable media, and requires a documented business need for access. Sharing Restricted data with a third
party requires a signed data processing agreement and Security approval.

## 4. Endpoint Security

Company laptops must have full-disk encryption, the managed endpoint-protection agent and automatic OS
updates enabled. Screens must lock after **5 minutes** of inactivity. Lost or stolen devices must be
reported to the IT service desk within 1 hour so they can be remotely wiped.

## 5. Robot Fleet Security

Robot firmware must be cryptographically signed; robots refuse unsigned updates. Remote fleet access is
only possible through the fleet management portal with MFA, and every remote session is recorded.

## 6. Reporting Security Incidents

Suspected security incidents — phishing, malware, lost devices, unexpected access — must be reported to
the Security team within **1 hour** of discovery via the #security-incident channel or the security
hotline. Do not attempt to investigate on your own. Incident handling follows the Incident Response
Runbook.

## 7. Exceptions and Enforcement

Exceptions require a written risk acceptance signed by the CISO and are valid for at most 6 months.
Violations of this policy may result in disciplinary action up to and including termination.
