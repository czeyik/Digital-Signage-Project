# Android Build Verification

The Android player requires JDK 17 and Android SDK 36. If the host machine does
not have that toolchain, build and run the checked-in verification container
from the repository root:

```sh
docker build -f android-player/Dockerfile.build -t duducar-android-build android-player
docker run --rm \
  --mount type=bind,source="$PWD/android-player",target=/source,readonly \
  --tmpfs /workspace:exec,size=3g \
  -e GRADLE_USER_HOME=/tmp/gradle-home \
  -w /workspace duducar-android-build /bin/bash -lc \
  'cp -a /source/. /workspace/; rm -rf /workspace/app/build /workspace/build /workspace/.gradle /workspace/.kotlin; ./gradlew testDevelopmentDebugUnitTest lintDevelopmentDebug :app:assembleDevelopmentDebug :app:compileDevelopmentDebugAndroidTestKotlin --no-daemon'
```

The source mount is read-only and all generated files are discarded with the
container. This prevents root-owned build output or Gradle/Android home folders
from appearing in the repository.

Only `developmentDebug` and `productionRelease` variants exist. Development
uses the separate `com.duducar.signage.development` application ID and an
intentionally unusable API hostname unless an isolated development endpoint is
provided:

```sh
./gradlew :app:assembleDevelopmentDebug \
  -PdevelopmentApiBaseUrl=https://api.example-development.invalid/api/v1/ \
  -PdevelopmentPlayIntegrityProjectNumber=123456789012 \
  --no-daemon
```

The checked JVM tests cover enrollment, manifest activation/restart,
battery-powered playback with no external-power gate, planned-shutdown state,
storage, queue loss, PIN verification, PIN throttling, the bounded
administrator session, monotonic server-corrected time, proof recovery,
abnormal-exit diagnostic mapping/idempotency, and gzip proof-batch encoding.
Instrumentation sources currently verify merged-manifest backup configuration,
absence of static boot/power receivers, administrator-relock receiver exposure,
the date/time device-owner restriction, atomic checkpoint/batch persistence,
and planned-shutdown marker/event durability. They do not exercise
`MainActivity`'s visible non-PIN shutdown confirmation, neutral stopped screen,
no-automatic-resume behavior, or the visible non-PIN **Resume DUDU**
confirmation after launch. Those flows should be executed on an Android 12+
emulator and, when practical, on the selected display model. Physical battery
behavior, physical shutdown/recovery, and Android-13 exit-reason behavior are
optional operator observations under the simplified hardware-registration
policy.

CI additionally generates a disposable test-only keystore, builds and lints the
configured minified `productionRelease`, and verifies its APK signature. This
exercises the release path without exposing or replacing the real company
signing key. It also proves that unsigned production builds and development
builds aimed at either canonical or trailing-dot production hostnames fail
closed.
