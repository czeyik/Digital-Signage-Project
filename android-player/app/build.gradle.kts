import java.net.URI

plugins {
    id("com.android.application")
}

fun quotedBuildConfig(value: String): String =
    "\"${value.replace("\\", "\\\\").replace("\"", "\\\"")}\""

val requiredProductionApiBaseUrl = "https://api.marketing.duducaradmin.com/api/v1/"
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
val developmentApiBaseUrl = providers.gradleProperty("developmentApiBaseUrl").orNull
    ?: "https://localhost.invalid/api/v1/"
val productionApiBaseUrl = providers.gradleProperty("productionApiBaseUrl").orNull
    ?: "https://production-configuration-required.invalid/api/v1/"
val playIntegrityProjectNumber =
    providers.gradleProperty("playIntegrityProjectNumber").orNull?.toLongOrNull() ?: 0L
val developmentPlayIntegrityProjectNumber =
    providers.gradleProperty("developmentPlayIntegrityProjectNumber")
        .orNull
        ?.toLongOrNull()
        ?: 0L
val productionVersionCodeInput = providers.gradleProperty("productionVersionCode").orNull
val parsedProductionVersionCode = productionVersionCodeInput?.toIntOrNull()
val productionVersionCode = parsedProductionVersionCode ?: 1
val previousProductionVersionCode =
    providers.gradleProperty("previousProductionVersionCode").orNull?.toIntOrNull() ?: -1
val productionVersionName = providers.gradleProperty("productionVersionName").orNull
    ?: "production-version-required"
val productionHosts = setOf(
    "marketing.duducaradmin.com",
    "api.marketing.duducaradmin.com",
)

android {
    namespace = "com.duducar.signage"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.duducar.signage"
        minSdk = 31
        targetSdk = 36
        versionCode = 1
        versionName = "0.0.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
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

    flavorDimensions += "environment"
    productFlavors {
        create("development") {
            dimension = "environment"
            applicationIdSuffix = ".development"
            versionNameSuffix = "-development"
            buildConfigField("boolean", "IS_PRODUCTION", "false")
            buildConfigField(
                "String",
                "API_BASE_URL",
                quotedBuildConfig(developmentApiBaseUrl),
            )
            buildConfigField(
                "long",
                "PLAY_INTEGRITY_PROJECT_NUMBER",
                "${developmentPlayIntegrityProjectNumber}L",
            )
        }
        create("production") {
            dimension = "environment"
            versionCode = productionVersionCode
            versionName = productionVersionName
            buildConfigField("boolean", "IS_PRODUCTION", "true")
            buildConfigField(
                "String",
                "API_BASE_URL",
                quotedBuildConfig(productionApiBaseUrl),
            )
            buildConfigField(
                "long",
                "PLAY_INTEGRITY_PROJECT_NUMBER",
                "${playIntegrityProjectNumber}L",
            )
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

androidComponents {
    beforeVariants(selector().all()) { variant ->
        val environment = variant.productFlavors
            .firstOrNull { it.first == "environment" }
            ?.second
        // Only these two explicit artifacts exist. A debug command can never
        // accidentally build an APK configured for production.
        if (
            (environment == "production" && variant.buildType == "debug") ||
            (environment == "development" && variant.buildType == "release")
        ) {
            variant.enable = false
        }
    }
}

gradle.taskGraph.whenReady {
    val developmentRequested =
        allTasks.any { it.name.contains("DevelopmentDebug", ignoreCase = true) }
    val developmentUri = if (developmentRequested) {
        runCatching { URI(developmentApiBaseUrl) }.getOrElse {
            throw GradleException("Development API URL must be a valid absolute URL.")
        }
    } else {
        null
    }
    val developmentHost = developmentUri?.host?.lowercase()?.trimEnd('.')
    val developmentScheme = developmentUri?.scheme?.lowercase()
    if (
        developmentRequested &&
        (
            developmentUri?.isAbsolute != true ||
                developmentHost.isNullOrBlank() ||
                developmentScheme !in setOf("http", "https") ||
                developmentUri?.userInfo != null ||
                developmentUri?.query != null ||
                developmentUri?.fragment != null ||
                developmentUri?.path?.endsWith('/') != true
            )
    ) {
        throw GradleException(
            "Development API URL must be an absolute HTTP(S) URL with a host and trailing slash.",
        )
    }
    if (
        developmentRequested &&
        developmentHost in productionHosts
    ) {
        throw GradleException("Development builds may not target a production hostname.")
    }
    if (
        developmentRequested &&
        developmentUri?.scheme?.equals("http", ignoreCase = true) == true &&
        developmentUri?.host != "localhost"
    ) {
        throw GradleException(
            "Development cleartext is permitted only for http://localhost over ADB reverse.",
        )
    }
    val productionReleaseRequested =
        allTasks.any { it.name.contains("ProductionRelease", ignoreCase = true) }
    if (!productionReleaseRequested) return@whenReady

    if (!releaseSigningConfigured) {
        throw GradleException(
            "Production release signing requires DUDU_SIGNING_STORE_FILE, " +
                "DUDU_SIGNING_STORE_PASSWORD, DUDU_SIGNING_KEY_ALIAS, and " +
                "DUDU_SIGNING_KEY_PASSWORD.",
        )
    }
    if (releaseStoreFile == null || !file(releaseStoreFile).isFile) {
        throw GradleException("DUDU_SIGNING_STORE_FILE must identify an existing keystore.")
    }
    if (productionApiBaseUrl != requiredProductionApiBaseUrl) {
        throw GradleException(
            "-PproductionApiBaseUrl must explicitly equal $requiredProductionApiBaseUrl.",
        )
    }
    if (playIntegrityProjectNumber <= 0L) {
        throw GradleException(
            "-PplayIntegrityProjectNumber must be the non-zero numeric Google Cloud project number.",
        )
    }
    if (
        parsedProductionVersionCode == null ||
        productionVersionCode <= 0 ||
        previousProductionVersionCode < 0 ||
        productionVersionCode <= previousProductionVersionCode
    ) {
        throw GradleException(
            "Set -PpreviousProductionVersionCode to the deployed code and " +
                "-PproductionVersionCode to a strictly greater positive value.",
        )
    }
    if (!Regex("[0-9]+\\.[0-9]+\\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?")
            .matches(productionVersionName)
    ) {
        throw GradleException(
            "-PproductionVersionName must be an explicit semantic version such as 1.0.0.",
        )
    }
}

dependencies {
    implementation("com.google.android.play:integrity:1.6.0")
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test:runner:1.7.0")
    testImplementation("junit:junit:4.13.2")
}
