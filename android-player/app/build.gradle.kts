import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val releaseStoreFile = System.getenv("DUDU_SIGNING_STORE_FILE")
val releaseStorePassword = System.getenv("DUDU_SIGNING_STORE_PASSWORD")
val releaseKeyAlias = System.getenv("DUDU_SIGNING_KEY_ALIAS")
val releaseKeyPassword = System.getenv("DUDU_SIGNING_KEY_PASSWORD")
val releaseSigningConfigured = listOf(
    releaseStoreFile,
    releaseStorePassword,
    releaseKeyAlias,
    releaseKeyPassword,
).all { !it.isNullOrBlank() }

android {
    namespace = "com.duducar.signage"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.duducar.signage"
        minSdk = 31
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"

        buildConfigField(
            "String",
            "API_BASE_URL",
            "\"${providers.gradleProperty("apiBaseUrl").orNull ?: "https://api.marketing.duducaradmin.com/api/v1/"}\""
        )
        buildConfigField(
            "long",
            "PLAY_INTEGRITY_PROJECT_NUMBER",
            "${providers.gradleProperty("playIntegrityProjectNumber").orNull ?: "0"}L",
        )
    }

    buildFeatures {
        buildConfig = true
        viewBinding = true
    }

    signingConfigs {
        if (releaseSigningConfigured) {
            create("production") {
                storeFile = file(releaseStoreFile!!)
                storePassword = releaseStorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            if (releaseSigningConfigured) {
                signingConfig = signingConfigs.getByName("production")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

gradle.taskGraph.whenReady {
    if (allTasks.any { it.name.contains("Release", ignoreCase = true) } &&
        !releaseSigningConfigured
    ) {
        throw GradleException(
            "Release signing requires DUDU_SIGNING_STORE_FILE, " +
                "DUDU_SIGNING_STORE_PASSWORD, DUDU_SIGNING_KEY_ALIAS, and " +
                "DUDU_SIGNING_KEY_PASSWORD.",
        )
    }
}

dependencies {
    implementation("com.google.android.play:integrity:1.6.0")
    testImplementation("junit:junit:4.13.2")
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}
