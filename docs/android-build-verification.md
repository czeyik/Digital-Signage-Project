# Android Build Verification

The Android player requires JDK 17 and Android SDK 36. If the host machine does
not have that toolchain, build and run the checked-in verification container
from the repository root:

```sh
docker build -f android-player/Dockerfile.build -t duducar-android-build android-player
docker run --rm -v "$PWD/android-player:/workspace" -w /workspace duducar-android-build ./gradlew :app:compileDebugKotlin --no-daemon
```

Use the same container command before accepting player changes. The command is a
compile check only; hardware behavior still requires the qualification checklist
on the selected display model.
