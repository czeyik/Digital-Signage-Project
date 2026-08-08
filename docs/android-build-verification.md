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

The checked JVM tests cover enrollment, manifest activation/restart, power and
mode gates, storage, queue loss, PIN verification, PIN throttling, the bounded
administrator session, monotonic server-corrected time, proof recovery, and gzip
proof-batch encoding. Instrumentation sources verify merged-manifest backup,
receiver exposure, and the date/time device-owner restriction; compiling them
is CI-safe, but executing them still requires an Android 12+ emulator or tablet.
Hardware behavior still requires the qualification checklist on the selected
display model.

CI additionally generates a disposable test-only keystore, builds and lints the
configured minified `productionRelease`, and verifies its APK signature. This
exercises the release path without exposing or replacing the real company
signing key. It also proves that unsigned production builds and development
builds aimed at either canonical or trailing-dot production hostnames fail
closed.
