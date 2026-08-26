package com.duducar.signage

/** Pure policy decisions for the foreground-only location collector. */
object LocationPolicy {
    const val REPORT_INTERVAL_MS = 60_000L
    const val MAX_FIX_AGE_MS = 2 * 60_000L
    const val FRESH_STATE_MAX_AGE_MS = 3 * 60_000L
    const val STALE_STATE_MAX_AGE_MS = 10 * 60_000L
    const val MAX_ACCURACY_METERS = 100.0
    const val QUEUE_CAPACITY = 50_000

    fun acceptsFix(
        provider: String?,
        accuracyMeters: Float,
        ageMs: Long,
        isMock: Boolean,
    ): Boolean =
        provider in setOf("gps", "network") &&
            accuracyMeters.isFinite() &&
            accuracyMeters >= 0f &&
            accuracyMeters <= MAX_ACCURACY_METERS &&
            ageMs in 0..MAX_FIX_AGE_MS &&
            !isMock

    fun stateFor(lastValidAgeMs: Long?): String = when {
        lastValidAgeMs == null -> "initializing"
        lastValidAgeMs < FRESH_STATE_MAX_AGE_MS -> "fresh"
        lastValidAgeMs < STALE_STATE_MAX_AGE_MS -> "stale"
        else -> "unavailable"
    }

    fun stateForNoFix(collectionAgeMs: Long?): String = when {
        collectionAgeMs == null || collectionAgeMs < FRESH_STATE_MAX_AGE_MS -> "initializing"
        collectionAgeMs < STALE_STATE_MAX_AGE_MS -> "stale"
        else -> "unavailable"
    }
}

object LocationCollectionStates {
    const val INITIALIZING = "initializing"
    const val FRESH = "fresh"
    const val STALE = "stale"
    const val UNAVAILABLE = "unavailable"
    const val PLANNED_GAP = "planned_gap"
    const val SHUTDOWN = "shutdown"
    const val PERMISSION_DISABLED = "permission_disabled"
    const val LOCATION_DISABLED = "location_disabled"
    const val MOCK = "mock"
}
