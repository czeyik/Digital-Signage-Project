# DUDU Car Digital Signage

Secure in-car advertising software for DUDU Car Malaysia. The pilot manages
silent weekly advertising playlists for 10 Android displays and is designed to
grow toward 1,000 devices.

This repository currently contains a runnable pilot foundation:

- A Django desktop dashboard and versioned device API
- PostgreSQL development configuration
- Quarantined JPEG, PNG, and MP4 upload processing
- Immutable weekly playlists and urgent replacements
- One-time Android device enrollment and short-lived access tokens
- Offline playlist caching and queued proof-of-play batches
- Fleet health, alerts, CSV reporting, and audit events
- A native Android 12+ kiosk player

Read [AGENTS.md](AGENTS.md) before changing product behavior or security rules.
The architecture decision is in [docs/architecture.md](docs/architecture.md).

## What You Need

For the backend:

- Ubuntu 22.04 or another modern Linux distribution
- Python 3.10 or newer
- Docker with Docker Compose
- FFmpeg and FFprobe
- ClamAV for production media processing

For Android:

- Android Studio with JDK 17
- Android SDK 36
- An Android 12+ phone, emulator, or approved signage tablet

The current machine has Java 11, so install JDK 17 before building the Android
application.

## Start The Backend

1. Create the local Python environment:

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install --upgrade pip
   .venv/bin/pip install -e './backend[dev]'
   ```

2. Start PostgreSQL:

   ```bash
   docker compose up -d db
   ```

3. Export the local database settings:

   ```bash
   export DATABASE_URL='postgresql://signage:local-development-only@localhost:5432/signage'
   export DB_SSLMODE=disable
   export DJANGO_SECRET_KEY='replace-this-with-a-long-random-development-secret'
   ```

4. Create the schema and first owner:

   ```bash
   .venv/bin/python backend/manage.py migrate
   .venv/bin/python backend/manage.py create_initial_owner \
     --email owner@duducar.co \
     --password 'replace-with-a-strong-unique-password'
   ```

5. Start the dashboard:

   ```bash
   .venv/bin/python backend/manage.py runserver
   ```

Open `http://127.0.0.1:8000/`. Local development may use SQLite when
`DATABASE_URL` is absent, but PostgreSQL is the supported shared environment.

Never use the example passwords or development secret in production.

## First Pilot Workflow

1. Sign in as the account owner.
2. Add drivers, vehicles, devices, and assignments in `/admin/`.
3. Upload media from the dashboard.
4. Process quarantined media:

   ```bash
   .venv/bin/python backend/manage.py process_media
   ```

   Production processing fails closed when ClamAV is unavailable. For a local
   machine without ClamAV only:

   ```bash
   .venv/bin/python backend/manage.py process_media --allow-missing-clamav
   ```

5. Create and publish a playlist.
6. Generate a one-time device enrollment code.
7. Enter that code in the Android player.

The normal weekly window is Monday 12:00 PM through the next Monday 11:59:59 AM
in `Asia/Kuala_Lumpur`. The account owner can change the tentative playlist
entry and duration limits under **Administration > Platform settings**.

## Scheduled Operations

Run these commands from a scheduler in production:

```bash
# At least every 30 minutes
.venv/bin/python backend/manage.py evaluate_device_health

# Daily
.venv/bin/python backend/manage.py apply_retention
```

Database and object-storage backups must run daily and be retained for 30 days.
Restore procedures must be tested before the pilot is treated as production.

## Android Player

Open `android-player/` in Android Studio. The default API is:

```text
https://api.marketing.duducaradmin.com/api/v1/
```

For local testing, set a Gradle property to an HTTPS development endpoint:

```text
apiBaseUrl=https://your-development-api.example/api/v1/
```

The app intentionally requests no GPS, camera, microphone, contacts, or shared
storage permission. It stores the device refresh credential with Android
Keystore, downloads media atomically, verifies SHA-256 checksums, queues
playback batches in SQLite, and preserves maintenance mode across restarts.

Kiosk setup and automatic boot are hardware-dependent. Do not deploy a tablet
until it passes [the hardware qualification checklist](docs/hardware-qualification.md).

## Verification

Run backend checks:

```bash
cd backend
../.venv/bin/ruff check .
../.venv/bin/pytest
../.venv/bin/python manage.py check
../.venv/bin/python manage.py makemigrations --check --dry-run
```

Run the production security check with production-like environment variables:

```bash
DJANGO_DEBUG=false \
DJANGO_SECRET_KEY='a-random-secret-longer-than-fifty-characters-goes-here' \
DJANGO_ALLOWED_HOSTS=marketing.duducaradmin.com \
DJANGO_SECURE_SSL_REDIRECT=true \
../.venv/bin/python manage.py check --deploy
```

## Important Pilot Limits

- MFA is deferred and remains a post-pilot security priority.
- Proof-of-play is commercially useful but not independently audited or
  tamper-proof.
- Root detection is a defense-in-depth signal, not a guarantee against a
  sophisticated compromised device.
- True physical screen state, temperature, automatic power-on, and shutdown
  behavior require qualified hardware.
- AWS infrastructure is not provisioned by this repository yet.
- Android release signing keys and production secrets must never be committed.

News, weather, GPS targeting, PDF reports, animated media, advertiser accounts,
approval workflows, remote app updates, and advanced fleet management are
outside the pilot.
