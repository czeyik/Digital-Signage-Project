# Device API Summary

All device routes are under `/api/v1/` and use JSON.

## Enrollment

`POST devices/enroll/`

Consumes a six-digit, single-use, 15-minute enrollment code plus Android and
integrity metadata. Returns a device-specific refresh credential and a one-hour
access token. The refresh credential must be stored with Android Keystore.

`POST devices/token/`

Exchanges a valid, non-revoked refresh credential for a short-lived access
token.

## Operations

`GET devices/sync/`

Returns server time and one of:

- `play`: immutable playlist manifest and expiring media URLs
- `fallback`: bundled DUDU media should play
- `maintenance`: advertising must stop and maintenance state must persist

`POST devices/heartbeat/`

Accepts screen, external-power, battery, storage, application, Android, and
optional temperature state.

`POST devices/playback-batches/`

Accepts an idempotent loop batch containing one result per playlist entry.
Duplicate batch IDs are acknowledged without creating duplicate evidence.
Disabled devices cannot submit playback.

## Security

Use TLS only. Never log bearer or refresh tokens. Server authorization derives
the device from the access token and ignores any client-supplied device or
assignment identity.

