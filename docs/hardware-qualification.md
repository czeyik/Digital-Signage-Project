# Exact Android Display Qualification

Record the exact model **and firmware** before purchase or production
enrollment. Record model, a physical corner-to-corner display diagonal to two
decimal places (excluding the bezel), firmware/build, Android version/security
patch, date, tester, APK version, photos/logs/video/notes, and an evidence
location. Android-reported DPI or diagonal estimates are not physical evidence.
An owner creates the `HardwareQualification` record in `/admin/`. Production
enrollment requires an exact identity record with a nonempty evidence reference
and a recorded 9.00–12.00-inch measurement; the physical pass fields remain
tracked but do not block enrollment. `approved_for_pilot` still requires every
current required field to be true and complete evidence. Emulators, phones, and
a different firmware never qualify the exact identity record.
The approval evidence is immutable: revoke it and create a new record rather
than editing a previously approved physical build.

A display-policy migration revokes an approval that lacks a compliant physical
measurement. Before deploying that migration, inventory affected active devices
and obtain maintenance approval: they enter maintenance on their next sync. A
revoked record cannot be amended or re-approved.

During production enrollment, the player submits its `hardware_model`,
`firmware_version`, and `security_patch_level` in the integrity-bound challenge.
All three must exactly match the selected enrollment-eligible record; the server
persists that binding on the enrolled device. A changed build or security patch
requires fresh qualification and owner-issued enrollment. Completing the 19
physical pass fields remains required for `approved_for_pilot`, not enrollment
or sync.

## Entry conditions

- Android 12+ with current security updates; 9–12-inch landscape (inclusive),
  battery-backed display with LTE; factory-reset device-owner/lock-task
  provisioning; signed release APK; appropriate SIM, mount, cable/fuse/adapter
  and safe test area.
- Preserve the exact device and tester identities. Do not install an unreviewed
  firmware or third-party image. Play Protect certification and signed-APK
  `MEETS_DEVICE_INTEGRITY` remain separate production-enrollment gates.
- The legacy `legacy_boot_on_vehicle_power_passed` and
  `legacy_external_power_loss_path_passed` fields are historical only. They
  never approve a new record or restore the former vehicle-power policy.

## Required evidence and pass criteria

Mark and evidence each current database field:

1. `device_owner_lock_task_passed`, `kiosk_escape_resistance_passed`, and
   `screen_state_passed`: DUDU is persistent Home; navigation/status controls,
   safe mode, users, force-stop, overlays, date/time, USB/media transfer and
   accessibility paths cannot escape policy; visual and reported screen state
   agree.
2. `battery_backed_playback_passed`, `battery_runtime_passed`, and
   `battery_level_telemetry_passed`: unplug external power during every media
   type without interruption; record safe runtime/charging; reliable 0–100%
   produces one `low_battery` warning at <=20% and escalates it at <=10% without
   stopping playback. Never require/infer external power or charging state.
3. `planned_shutdown_flow_passed` and `physical_shutdown_recovery_passed`:
   visible non-PIN **Prepare for shutdown** confirms, queues one interruption,
   shows neutral stopped state, and never auto-resumes; only visible non-PIN
   **Resume DUDU** restarts. Test documented physical power-off then
   battery-depletion/reboot: staff unlocks/launches to stopped state and resumes.
4. `abnormal_exit_recovery_passed`: kill at item/loop boundaries; no false
   completed play, every result queues exactly once, and playback recovers. On
   Android 13+, test stable idempotent abnormal-exit events; on Android 12,
   record that platform limitation and safe checkpoint recovery.
5. `playback_12h_passed`, `image_aspect_passed`, `cache_capacity_passed`,
   `network_reconnect_passed`, and `interrupted_download_passed`: smooth 1080p
   H.264 for 12 hours; JPEG/PNG uncropped; 10 GiB cache/500 MiB queue with >=2
   GiB free; LTE tunnel/dead-zone/handover recovery; interrupted/corrupt media
   preserves the last valid playlist and correctly resumes upload/download.
6. `thermal_passed` and `mounting_power_safety_passed`: representative-car
   temperature is within manufacturer limits, battery protection is sound, and
   mount/cable/fuse/adapter create no road-safety hazard.
7. `device_time_change_passed`, `remote_disable_reboot_passed`, and
   `factory_reset_revocation_passed`: kiosk blocks wall-clock change and the
   monotonic server-time anchor remains stable; remote disable revokes credentials
   and offline reboot remains at non-playing re-enrollment; reset plus server
   revocation blocks old credentials.

## Adversarial checks

- Test wrong PIN throttling (4 failures; then 1/5/15-minute lockouts), correct
  PIN automatic relock and **Relock now**; kill DUDU during admin Settings and
  ensure the exact-alarm relock still applies. Removing alarm permission must
  refuse admin mode rather than rely on an in-process timer.
- Test same-key higher-version sideload and approved ADB/factory-reset recovery.
  With staff-controlled USB debugging enabled, ADB must remain usable before
  enrollment; after enrollment, only a PIN-authenticated administrator session
  may temporarily restore USB transfer, and manual or timed relock must block it
  again. These checks do not authorize global USB debugging. Test cloud and
  device-to-device transfer: tokens, PIN verifier/state, cache, manifests and
  evidence queue must not transfer.
- Verify offline download/activation/playback/event upload, corrupt cache,
  remote-disable reboot, all kiosk escape routes, and one complete loop's
  idempotent evidence. Never count fallback or unplayed time as proof.

## Operating rule

Do not leave displays in parked vehicles. Define removal, storage, inspection,
damaged-battery isolation, incident and approved physical power-off procedures
before use. Battery/parked playback may count as a completed play, but proves
neither vehicle operation, occupancy, external power nor audience exposure.
