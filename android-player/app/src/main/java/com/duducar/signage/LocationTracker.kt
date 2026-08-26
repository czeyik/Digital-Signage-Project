package com.duducar.signage

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import org.json.JSONObject
import java.time.Instant
import java.util.UUID

/**
 * Foreground-only location collection. It owns no service and registers native
 * GPS/network listeners only while the enrolled player activity is visible.
 */
class LocationTracker(
    context: Context,
    private val credentials: CredentialStore,
    private val kioskPolicies: KioskPolicyManager,
    private val store: PlayerStore,
    private val serverClock: ServerClock,
    private val requestUpload: () -> Unit,
) {
    private val appContext = context.applicationContext
    private val locationManager = appContext.getSystemService(LocationManager::class.java)
    private val handler = Handler(Looper.getMainLooper())
    private var foreground = false
    private var adminSession = false
    private var shutdown = false
    private var registered = false
    private var mockDetected = false
    private var lastValidLocation: Location? = null
    private var plannedGapUntilEpochMs: Long? = null
    private var collectionStartedElapsedMs: Long? = null

    private val listener = object : LocationListener {
        override fun onLocationChanged(location: Location) {
            onLocation(location)
        }
    }

    private val sample = object : Runnable {
        override fun run() {
            sampleOnce()
            if (shouldCollect()) {
                handler.postDelayed(this, LocationPolicy.REPORT_INTERVAL_MS)
            }
        }
    }

    fun onForegroundChanged(visible: Boolean) {
        foreground = visible
        refreshRegistration()
    }

    fun beginPlannedGap() {
        adminSession = true
        stopUpdates()
        plannedGapUntilEpochMs = serverClock.now()
            .plusMillis(PLANNED_GAP_TOTAL_MS)
            .toEpochMilli()
        reportState(LocationCollectionStates.PLANNED_GAP)
    }

    fun endAdminSession() {
        adminSession = false
        plannedGapUntilEpochMs = serverClock.now()
            .plusMillis(REACQUISITION_SUPPRESSION_MS)
            .toEpochMilli()
        reportState(LocationCollectionStates.PLANNED_GAP)
        refreshRegistration()
    }

    fun markShutdown() {
        shutdown = true
        stopUpdates()
        plannedGapUntilEpochMs = null
        reportState(LocationCollectionStates.SHUTDOWN)
    }

    fun resumeAfterShutdown() {
        shutdown = false
        plannedGapUntilEpochMs = null
        refreshRegistration()
    }

    fun destroy() {
        stopUpdates()
        handler.removeCallbacksAndMessages(null)
    }

    private fun shouldCollect(): Boolean =
        foreground && !adminSession && !shutdown && credentials.hasRefreshToken()

    private fun refreshRegistration() {
        if (!shouldCollect()) {
            stopUpdates()
            return
        }
        kioskPolicies.ensureLocationAccess()
        if (!hasFinePermission()) {
            stopUpdates()
            reportState(LocationCollectionStates.PERMISSION_DISABLED)
            scheduleNextSample()
            return
        }
        if (!isLocationEnabled()) {
            stopUpdates()
            reportState(LocationCollectionStates.LOCATION_DISABLED)
            scheduleNextSample()
            return
        }
        if (collectionStartedElapsedMs == null) {
            collectionStartedElapsedMs = SystemClock.elapsedRealtime()
        }
        if (!registered) {
            try {
                var requested = false
                listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER).forEach {
                    if (locationManager.isProviderEnabled(it)) {
                        locationManager.requestLocationUpdates(
                            it,
                            1_000L,
                            0f,
                            listener,
                            Looper.getMainLooper(),
                        )
                        locationManager.getLastKnownLocation(it)?.let(::onLocation)
                        requested = true
                    }
                }
                registered = requested
            } catch (_: SecurityException) {
                registered = false
            }
        }
        scheduleNextSample(immediate = true)
    }

    private fun scheduleNextSample(immediate: Boolean = false) {
        handler.removeCallbacks(sample)
        handler.postDelayed(sample, if (immediate) 0L else LocationPolicy.REPORT_INTERVAL_MS)
    }

    private fun stopUpdates() {
        handler.removeCallbacks(sample)
        if (registered) {
            runCatching { locationManager.removeUpdates(listener) }
            registered = false
        }
    }

    private fun hasFinePermission(): Boolean =
        appContext.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED

    private fun isLocationEnabled(): Boolean =
        runCatching { locationManager.isLocationEnabled }.getOrDefault(false)

    private fun onLocation(location: Location) {
        if (!shouldCollect()) return
        if (location.isMock) {
            mockDetected = true
            reportState(LocationCollectionStates.MOCK)
            return
        }
        val ageMs = locationAgeMs(location)
        if (
            !LocationPolicy.acceptsFix(
                provider = location.provider,
                accuracyMeters = location.accuracy,
                ageMs = ageMs,
                isMock = false,
            ) || !location.latitude.isFinite() || !location.longitude.isFinite()
        ) return
        mockDetected = false
        lastValidLocation = Location(location)
    }

    private fun sampleOnce() {
        if (!shouldCollect()) return
        if (!hasFinePermission()) {
            stopUpdates()
            reportState(LocationCollectionStates.PERMISSION_DISABLED)
            return
        }
        if (!isLocationEnabled()) {
            stopUpdates()
            reportState(LocationCollectionStates.LOCATION_DISABLED)
            return
        }
        val location = lastValidLocation
        val ageMs = location?.let(::locationAgeMs)
        if (
            location != null &&
            LocationPolicy.acceptsFix(
                provider = location.provider,
                accuracyMeters = location.accuracy,
                ageMs = ageMs ?: Long.MAX_VALUE,
                isMock = location.isMock,
            ) &&
            location.latitude.isFinite() &&
            location.longitude.isFinite()
        ) {
            val recordedAt = serverClock.now().minusMillis(ageMs ?: 0L)
            val point = JSONObject()
                .put("id", UUID.randomUUID().toString())
                .put("recorded_at", recordedAt.toString())
                .put(
                    "device_recorded_at",
                    if (location.time > 0) {
                        Instant.ofEpochMilli(location.time).toString()
                    } else {
                        recordedAt.toString()
                    },
                )
                .put("latitude", location.latitude)
                .put("longitude", location.longitude)
                .put("accuracy_m", location.accuracy.toDouble())
                .put("provider", location.provider)
                .put("source", SOURCE)
            runCatching {
                store.enqueueLocationPoint(
                    point = point,
                    recordedAtEpochMs = recordedAt.toEpochMilli(),
                    recordedAt = serverClock.now().toString(),
                )
            }.onFailure {
                reportState(LocationCollectionStates.UNAVAILABLE)
                return
            }
            store.setLocationCurrentPointId(point.getString("id"))
            reportState(LocationCollectionStates.FRESH)
            return
        }
        val lastValidAge = lastValidLocation?.let(::locationAgeMs)
        val collectionAge = collectionStartedElapsedMs?.let {
            (SystemClock.elapsedRealtime() - it).coerceAtLeast(0L)
        }
        reportState(
            when {
                mockDetected -> LocationCollectionStates.MOCK
                lastValidAge != null -> LocationPolicy.stateFor(lastValidAge)
                else -> LocationPolicy.stateForNoFix(collectionAge)
            },
        )
        if (!registered) refreshRegistration()
    }

    private fun reportState(state: String) {
        if (!credentials.hasRefreshToken()) return
        val now = serverClock.now()
        val inPlannedGap = plannedGapUntilEpochMs?.let { it > now.toEpochMilli() } == true
        val effectiveState = when {
            state == LocationCollectionStates.SHUTDOWN -> state
            inPlannedGap -> LocationCollectionStates.PLANNED_GAP
            else -> state
        }
        val payload = JSONObject()
            .put("state", effectiveState)
            .put("reported_at", now.toString())
        plannedGapUntilEpochMs
            ?.takeIf { it > now.toEpochMilli() && effectiveState == LocationCollectionStates.PLANNED_GAP }
            ?.let { payload.put("planned_gap_until", Instant.ofEpochMilli(it).toString()) }
        store.setLocationCollectionState(payload)
        requestUpload()
    }

    private fun locationAgeMs(location: Location): Long {
        val elapsedNanos = location.elapsedRealtimeNanos
        if (elapsedNanos > 0L) {
            return ((SystemClock.elapsedRealtimeNanos() - elapsedNanos) / 1_000_000L)
                .coerceAtLeast(0L)
        }
        return (System.currentTimeMillis() - location.time).coerceAtLeast(0L)
    }

    companion object {
        private const val SOURCE = "location_manager"
        const val ADMIN_SESSION_SUPPRESSION_MS = 5 * 60_000L
        const val REACQUISITION_SUPPRESSION_MS = 2 * 60_000L
        const val PLANNED_GAP_TOTAL_MS =
            ADMIN_SESSION_SUPPRESSION_MS + REACQUISITION_SUPPRESSION_MS
    }
}
