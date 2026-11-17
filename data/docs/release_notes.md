# Helios OS Release Notes

Helios OS runs on every Helios robot and on Helios Cloud. Releases ship roughly every quarter;
customers on Premier support receive beta builds two weeks early.

## Helios OS 4.4 — September 15, 2026

### New features
- **Single sign-on (SSO)** for Helios Cloud via SAML 2.0 and OIDC, with SCIM user provisioning.
- Vega Vision Kit depth accuracy improved by **22%** at ranges beyond 2 metres.
- Mission scheduler supports time-window constraints for pick-up and drop-off.

### Fixes
- Fixed a rare case where Atlas robots paused at elevator doors for longer than 60 seconds.
- Fixed CSV export of fleet utilisation reports truncating at 10,000 rows.

### Known issues
- Orion Arm force-torque calibration must be re-run after upgrading from 4.2 directly to 4.4.

## Helios OS 4.3 — June 2, 2026

### New features
- **Force-torque sensing** for the Orion Arm wrist, enabling compliant assembly tasks.
- Battery health dashboard showing cycle count and estimated remaining capacity for every Atlas robot.
- Fleet API v2 is generally available.

### Deprecations
- **Fleet API v1 is deprecated** and will be removed in Helios OS 5.0. Migrate to Fleet API v2.

## Helios OS 4.2 — March 10, 2026

### New features
- Multi-site fleet management: one Helios Cloud workspace can manage robots across sites.
- Atlas AMR top speed increased from **1.5 m/s to 1.8 m/s** in aisles marked as high-speed zones.
- Improved dynamic obstacle avoidance around forklifts and pallet jacks.

### Fixes
- Docking success rate improved from 97.1% to 99.4% on uneven floors.

## Helios OS 4.1 — December 2, 2025

- Nova Controller support for up to 8 devices per work cell (previously 4).
- Audit log export to customer SIEM tools.
