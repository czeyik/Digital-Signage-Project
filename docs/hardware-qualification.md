# Android Display Qualification

Complete this checklist for the exact model and firmware before purchasing the
10 pilot units. Record model, firmware, test date, tester, and evidence.
Create a `HardwareQualification` record in the dashboard administration area
for each tested model/firmware. Do not mark `approved_for_pilot` until every
required and failure test has passed and the evidence reference points to the
supporting photos, logs, and notes.

The `legacy_boot_on_vehicle_power_passed` and
`legacy_external_power_loss_path_passed` fields preserve results from the
retired vehicle-power policy and do **not** qualify a new record. A new record
must instead pass the corresponding criteria for
`battery_backed_playback_passed`, `battery_runtime_passed`,
`battery_level_telemetry_passed`, `planned_shutdown_flow_passed`,
`physical_shutdown_recovery_passed`, and `abnormal_exit_recovery_passed`, as
well as the existing kiosk, screen, media, network, thermal, safety, and
recovery criteria.

## Required

- Android 12 or newer with current security updates
- Battery-backed 10-inch landscape display and built-in 4G LTE
- Device-owner provisioning and lock-task mode work after factory reset
- Navigation and status controls cannot escape the kiosk
- DUDU remains the persistent Home application while the tablet is unlocked
- Safe boot, secondary-user creation, app force-stop/control, overlay windows,
  manual date/time changes, physical-media mounting, and USB file transfer are
  blocked by the recorded device-owner policy
- Physical screen-on state is available and matches visual inspection
- Locked boot exposes no credential-protected player state. After a planned
  shutdown and battery depletion/reboot, staff unlock and launch DUDU to reach
  the shutdown-ready screen; playback resumes only after the visible non-PIN
  **Resume DUDU** confirmation, not from launcher or lifecycle restoration
- **Battery-backed playback:** advertising continues without interruption when
  external vehicle or cable power is disconnected; playback and keep-screen-on
  behavior do not depend on external-power detection
- **Battery runtime:** record battery protection, charging behavior, and a
  safe representative runtime/charging test suitable for the intended duty
  period
- **Battery-level telemetry:** Android reports a reliable 0–100 percent value
  and a controlled `<=20%` then `<=10%` test reaches the dashboard as the same
  `low_battery` warning then critical alert without stopping advertising
- **Planned shutdown flow:** a normal visible **Prepare for shutdown** button
  requires confirmation but no administrator PIN, records the interrupted item,
  shows a neutral shutdown-ready screen, and remains stopped with no five-minute
  automatic resume; only the visible non-PIN **Resume DUDU** confirmation may
  restart advertising
- **Physical shutdown recovery:** the exact documented physical power-off
  method is safe and reproducible; after battery depletion/reboot, staff can
  unlock and launch DUDU to reach the shutdown-ready screen, then deliberately
  confirm **Resume DUDU**. This is best-effort recovery, not a claim of
  automatic vehicle-power boot
- **Abnormal-exit recovery:** an unexpected app exit produces no false completed
  play and recovers safely. On Android 13+, verify each supported historical
  process-exit reason is queued idempotently as an `abnormal_app_exit`
  diagnostic; on Android 12, record the platform limitation and verify the
  recovery behavior
- H.264 1080p playback is smooth for a continuous 12-hour test
- JPEG and PNG display correctly without cropping
- 10 GB cache and 500 MB evidence queue fit with at least 2 GB free
- SIM reconnects after tunnel, dead-zone, and network handover tests
- Device remains stable during interrupted and resumed downloads
- Thermal behavior stays within manufacturer limits in a representative car
- Battery protection and charging behavior are suitable for 10-12 hour shifts
- Approved mount, cable, fuse, and adapter do not create a road-safety hazard

## Failure Tests

- Disconnect external vehicle or cable power during every media type. Playback
  must continue on battery, and no external-power or charging telemetry may be
  required for the result.
- At a safely controlled battery level, verify `<=20%` creates a `low_battery`
  warning and `<=10%` escalates that same unresolved alert to critical; neither
  threshold may stop playback.
- Use **Prepare for shutdown** during an item, confirm it without a PIN, and
  verify one interrupted result, a neutral shutdown-ready screen, no automatic
  five-minute resume, and no restart until the visible non-PIN **Resume DUDU**
  confirmation. Merely relaunching or restoring DUDU must not restart playback.
- Follow the model-specific physical power-off instructions, then test a
  battery-depletion/reboot recovery: staff unlock and launch DUDU to reach the
  shutdown-ready screen, then confirm **Resume DUDU** before it resumes. Do not
  claim or test automatic restart when vehicle power returns.
- Disconnect data during download, activation, playback, and event upload.
- Kill the DUDU process immediately before and after an item completes and at a
  loop boundary. Confirm the interrupted/orphaned loop is queued exactly once,
  every entry has a result, and playback resumes at the correct entry without
  counting unplayed time as played time. On Android 13+, verify the expected
  non-sensitive abnormal-exit diagnostic is queued with a stable event ID and
  is not double-counted after retry or restart.
- Corrupt a cached file and confirm the current playlist continues.
- Attempt kiosk escape through reboot, notifications, USB, accessibility,
  safe mode, and physical buttons.
- In maintenance or fallback, submit four wrong administrator PINs, then verify
  the fifth locks attempts for one minute, the sixth for five minutes, and
  subsequent failures for fifteen minutes. Reboot must not clear the throttle.
- Enter the correct administrator PIN, open Android settings, and verify the
  tablet automatically returns to locked DUDU playback within five minutes.
  Also verify the **Relock now** control. HOME ownership and screenshot blocking
  must remain active throughout the administrator session.
- Repeat the administrator-session test after killing the DUDU process while
  Android settings is foreground. The non-exported exact-alarm relock receiver
  must reapply device policy and return to DUDU at the original five-minute
  deadline. Remove **Alarms & reminders** access and confirm administrator mode
  refuses to open rather than relying only on an in-process timer.
- Verify staff can still sideload a same-key, higher-version APK and recover the
  tablet through the approved ADB/factory-reset procedure. The player does not
  disable factory reset or ADB globally because that would remove the pilot's
  unpaid recovery path; physical access remains a documented pilot risk.
- Attempt cloud backup and Android device-to-device transfer and confirm device
  tokens, administrator verifier/state, cached media, manifests, and proof queue
  are not restored to another tablet.
- Attempt to change date/time while the kiosk policy is active and confirm it is
  blocked. During a controlled qualification build, change wall time within one
  boot and confirm the monotonic server-time anchor keeps scheduling stable.
- Disable the device remotely and reboot it offline; maintenance mode must
  remain.
- Factory reset the device and confirm old credentials no longer grant access
  after server revocation.

## Operating Rule

Do not leave displays inside parked vehicles. Operations must define removal,
storage, inspection, damaged-battery isolation, and incident procedures before
the pilot begins. A fully completed advertisement while the tablet is
battery-powered or parked still counts as a completed play, but it proves
neither vehicle operation, vehicle occupancy, external power, nor audience
exposure.
