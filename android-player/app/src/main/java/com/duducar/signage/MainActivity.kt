package com.duducar.signage

import android.app.Activity
import android.app.ActivityManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.BitmapFactory
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.BatteryManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import com.duducar.signage.databinding.ActivityMainBinding
import org.json.JSONArray
import org.json.JSONObject
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.util.UUID
import java.util.concurrent.Executors

class MainActivity : Activity() {
    private lateinit var binding: ActivityMainBinding
    private lateinit var api: ApiClient
    private lateinit var cache: CacheManager
    private lateinit var store: PlayerStore
    private lateinit var serverClock: ServerClock
    private lateinit var integrity: IntegrityClient
    private lateinit var credentials: CredentialStore
    private lateinit var kioskPolicies: KioskPolicyManager
    private lateinit var adminRelockScheduler: AdminRelockScheduler
    private val executor = Executors.newSingleThreadExecutor()
    private val playbackHandler = Handler(Looper.getMainLooper())
    private val operationsHandler = Handler(Looper.getMainLooper())
    private val adminHandler = Handler(Looper.getMainLooper())
    private var activeManifest: JSONObject? = null
    private var currentIndex = 0
    private var currentStartedAt: Instant? = null
    private var currentStartedElapsedMs: Long? = null
    private var currentStartedBootCount: Int? = null
    private var currentResultId: String? = null
    private val loopResults = mutableListOf<PlaybackResult>()
    private var loopStartedAt: Instant? = null
    private var powerReceiverRegistered = false
    private var activityResumed = false
    private var resumeAfterAdminRelock = false
    private var playbackRecoveryBlocked = false

    private val adminRelock = Runnable { endAdminSession(bringToFront = true) }

    private val powerReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            updateKeepScreenOn(intent.action == Intent.ACTION_POWER_CONNECTED)
            if (playbackRecoveryBlocked) {
                showStatus(getString(R.string.playback_recovery_failed))
                return
            }
            if (intent.action == Intent.ACTION_POWER_DISCONNECTED) {
                interruptCurrent("external_power_lost")
                stopPlayback()
                if (!hasActiveAdminSession()) {
                    showStatus(getString(R.string.maintenance))
                }
            } else if (
                intent.action == Intent.ACTION_POWER_CONNECTED &&
                !hasActiveAdminSession()
            ) {
                synchronizeAndPlay()
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        credentials = CredentialStore(this)
        kioskPolicies = KioskPolicyManager(this)
        adminRelockScheduler = AdminRelockScheduler(this)
        api = ApiClient(credentials)
        cache = CacheManager(this)
        store = PlayerStore(this)
        serverClock = ServerClock(this)
        integrity = IntegrityClient(this)
        activeManifest = cache.activeManifest()
        val checkpointRecovered = recoverInterruptedPlayback()
        playbackRecoveryBlocked = !checkpointRecovered
        configureAdminControls()
        registerPowerReceiver()
        updateKeepScreenOn()

        if (!checkpointRecovered) {
            credentials.endAdminSession()
            enterLockedKiosk()
            showStatus(getString(R.string.playback_recovery_failed))
            return
        }

        if (hasActiveAdminSession()) {
            showAdminSessionControls()
            return
        }
        if (!enterLockedKiosk()) {
            showStatus(
                if (BuildConfig.IS_PRODUCTION) {
                    getString(R.string.device_owner_required)
                } else {
                    getString(R.string.kiosk_policy_failed)
                },
            )
            return
        }

        resumeConfiguredMode()
    }

    private fun configureAdminControls() {
        binding.root.setOnLongClickListener {
            val mode = store.state("device_mode")
            if (
                (mode == "maintenance" || mode == "fallback") &&
                credentials.hasKioskPinVerifier()
            ) {
                showAdminUnlock()
            }
            true
        }
        binding.adminUnlockButton.setOnClickListener {
            val now = System.currentTimeMillis()
            val remaining = credentials.pinLockoutRemainingMs(now)
            if (remaining > 0) {
                showPinLockout(remaining)
                return@setOnClickListener
            }
            val pin = binding.adminPin.text.toString()
            if (pin.length == 6 && credentials.verifyKioskPin(pin)) {
                credentials.clearPinFailures()
                startAdminSession(now)
            } else {
                binding.adminPin.text?.clear()
                val lockout = credentials.recordFailedPinAttempt(now)
                if (lockout > 0) {
                    showPinLockout(lockout)
                } else {
                    binding.adminError.text = "Invalid administrator PIN."
                }
            }
        }
        binding.openSettingsButton.setOnClickListener {
            if (hasActiveAdminSession()) {
                startActivity(Intent(Settings.ACTION_SETTINGS))
            }
        }
        binding.relockButton.setOnClickListener {
            endAdminSession(bringToFront = false)
        }
    }

    private fun registerPowerReceiver() {
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_POWER_CONNECTED)
            addAction(Intent.ACTION_POWER_DISCONNECTED)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(powerReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            registerReceiver(powerReceiver, filter)
        }
        powerReceiverRegistered = true
    }

    private fun resumeConfiguredMode() {
        if (playbackRecoveryBlocked) {
            showStatus(getString(R.string.playback_recovery_failed))
            return
        }
        if (BuildConfig.IS_PRODUCTION && !kioskPolicies.isDeviceOwner()) {
            showStatus(getString(R.string.device_owner_required))
            return
        }
        if (!credentials.hasRefreshToken()) {
            showEnrollment()
            return
        }
        when (store.state("device_mode")) {
            "maintenance" -> showStatus(getString(R.string.maintenance))
            "fallback" -> showFallback()
            else -> synchronizeAndPlay()
        }
        scheduleOperations()
    }

    override fun onDestroy() {
        if (powerReceiverRegistered) unregisterReceiver(powerReceiver)
        playbackHandler.removeCallbacksAndMessages(null)
        operationsHandler.removeCallbacksAndMessages(null)
        adminHandler.removeCallbacksAndMessages(null)
        executor.shutdownNow()
        super.onDestroy()
    }

    override fun onResume() {
        super.onResume()
        activityResumed = true
        if (!::credentials.isInitialized) return
        updateKeepScreenOn()
        if (hasActiveAdminSession()) {
            showAdminSessionControls()
        } else {
            adminRelockScheduler.cancel()
            val returningFromAdmin =
                resumeAfterAdminRelock || binding.adminControls.visibility == View.VISIBLE
            resumeAfterAdminRelock = false
            credentials.endAdminSession()
            if (enterLockedKiosk()) {
                binding.adminControls.visibility = View.GONE
                if (returningFromAdmin) resumeConfiguredMode()
            } else {
                showStatus(
                    if (BuildConfig.IS_PRODUCTION) {
                        getString(R.string.device_owner_required)
                    } else {
                        getString(R.string.kiosk_policy_failed)
                    },
                )
            }
        }
    }

    override fun onPause() {
        activityResumed = false
        super.onPause()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus && ::credentials.isInitialized && !hasActiveAdminSession()) {
            hideSystemUi()
        }
    }

    private fun showAdminUnlock() {
        val remaining = credentials.pinLockoutRemainingMs(System.currentTimeMillis())
        binding.adminPin.text?.clear()
        binding.adminError.text = ""
        binding.adminUnlock.visibility = View.VISIBLE
        if (remaining > 0) showPinLockout(remaining)
    }

    private fun showPinLockout(remainingMs: Long) {
        val seconds = ((remainingMs + 999) / 1000).coerceAtLeast(1)
        binding.adminError.text = "Too many attempts. Try again in $seconds seconds."
    }

    private fun startAdminSession(nowEpochMs: Long) {
        if (!adminRelockScheduler.mayScheduleExactAlarm()) {
            binding.adminError.text = getString(R.string.exact_alarm_required)
            return
        }
        credentials.beginAdminSession(nowEpochMs, SystemClock.elapsedRealtime())
        if (!adminRelockScheduler.schedule(KioskAdminPolicy.SESSION_DURATION_MS)) {
            credentials.endAdminSession()
            binding.adminError.text = getString(R.string.exact_alarm_required)
            return
        }
        operationsHandler.removeCallbacksAndMessages(null)
        interruptCurrent("administrator_session")
        stopPlayback()
        if (!kioskPolicies.relaxForAdminSession()) {
            adminRelockScheduler.cancel()
            credentials.endAdminSession()
            showStatus(getString(R.string.kiosk_policy_failed))
            return
        }
        try {
            stopLockTask()
        } catch (_: IllegalStateException) {
            // The activity was not in lock-task mode.
        } catch (_: SecurityException) {
            // The activity was not in lock-task mode.
        }
        showAdminSessionControls()
    }

    private fun showAdminSessionControls() {
        val remaining = adminSessionRemainingMs()
        if (remaining <= 0) {
            endAdminSession(bringToFront = false)
            return
        }
        if (!adminRelockScheduler.schedule(remaining)) {
            endAdminSession(bringToFront = false)
            return
        }
        stopPlayback()
        window.insetsController?.show(WindowInsets.Type.systemBars())
        binding.adminUnlock.visibility = View.GONE
        binding.enrollment.visibility = View.GONE
        binding.status.visibility = View.GONE
        binding.adminControls.visibility = View.VISIBLE
        adminHandler.removeCallbacks(adminRelock)
        adminHandler.postDelayed(adminRelock, remaining)
    }

    private fun endAdminSession(bringToFront: Boolean) {
        adminHandler.removeCallbacks(adminRelock)
        adminRelockScheduler.cancel()
        credentials.endAdminSession()
        binding.adminUnlock.visibility = View.GONE
        binding.adminControls.visibility = View.GONE
        if (!activityResumed && bringToFront) {
            if (!kioskPolicies.applyLockedPolicies()) {
                showStatus(getString(R.string.kiosk_policy_failed))
                return
            }
            resumeAfterAdminRelock = true
            startActivity(
                Intent(this, MainActivity::class.java).apply {
                    addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
                },
            )
            return
        }
        if (!enterLockedKiosk()) {
            showStatus(getString(R.string.kiosk_policy_failed))
            return
        }
        resumeConfiguredMode()
    }

    private fun hasActiveAdminSession(): Boolean =
        adminSessionRemainingMs() > 0

    private fun adminSessionRemainingMs(): Long = credentials.adminSessionRemainingMs(
        System.currentTimeMillis(),
        SystemClock.elapsedRealtime(),
    )

    private fun showEnrollment() {
        if (!EnrollmentPolicy.mayEnroll(BuildConfig.IS_PRODUCTION, kioskPolicies.isDeviceOwner())) {
            binding.enrollment.visibility = View.GONE
            showStatus(getString(R.string.device_owner_required))
            return
        }
        binding.status.visibility = View.GONE
        binding.enrollment.visibility = View.VISIBLE
        binding.enrollButton.setOnClickListener {
            if (!EnrollmentPolicy.mayEnroll(BuildConfig.IS_PRODUCTION, kioskPolicies.isDeviceOwner())) {
                binding.enrollment.visibility = View.GONE
                showStatus(getString(R.string.device_owner_required))
                return@setOnClickListener
            }
            val code = binding.enrollmentCode.text.toString()
            if (code.length != 6) {
                binding.enrollmentError.text = "Enter the six-digit code."
                return@setOnClickListener
            }
            binding.enrollButton.isEnabled = false
            executor.execute {
                try {
                    @Suppress("HardwareIds")
                    val androidId = Settings.Secure.getString(
                        contentResolver,
                        Settings.Secure.ANDROID_ID,
                    )
                    val challenge = api.enrollmentChallenge(code, androidId)
                    val integrityToken = integrity.token(
                        BuildConfig.PLAY_INTEGRITY_PROJECT_NUMBER,
                        challenge.getString("request_hash"),
                    )
                    val response = api.enroll(
                        challenge.getString("challenge_id"),
                        integrityToken,
                    )
                    serverClock.update(response.getString("server_time"))
                    runOnUiThread {
                        binding.enrollment.visibility = View.GONE
                        binding.enrollmentError.text = ""
                        synchronizeAndPlay()
                        scheduleOperations()
                    }
                } catch (error: Exception) {
                    runOnUiThread {
                        binding.enrollmentError.text = "Enrollment failed. Check the code and connection."
                        binding.enrollButton.isEnabled = true
                    }
                }
            }
        }
    }

    private fun synchronizeAndPlay() {
        if (playbackRecoveryBlocked) {
            showStatus(getString(R.string.playback_recovery_failed))
            return
        }
        if (hasActiveAdminSession()) return
        if (BuildConfig.IS_PRODUCTION && !kioskPolicies.isDeviceOwner()) {
            interruptCurrent("device_owner_removed")
            showStatus(getString(R.string.device_owner_required))
            return
        }
        if (!hasExternalPower()) {
            interruptCurrent("external_power_unavailable")
            showStatus(getString(R.string.maintenance))
            return
        }
        executor.execute {
            try {
                flushPendingBatches()
                val response = api.manifest()
                serverClock.update(response.getString("server_time"))
                when (response.getString("mode")) {
                    "maintenance" -> runOnUiThread {
                        if (hasActiveAdminSession()) return@runOnUiThread
                        markSuccessfulSync()
                        store.putState("device_mode", "maintenance")
                        interruptCurrent("device_disabled")
                        showStatus(getString(R.string.maintenance))
                    }
                    "fallback" -> runOnUiThread {
                        if (hasActiveAdminSession()) return@runOnUiThread
                        markSuccessfulSync()
                        store.putState("device_mode", "fallback")
                        interruptCurrent("fallback_mode")
                        showFallback()
                    }
                    "play" -> {
                        val manifest = response.getJSONObject("playlist")
                        if (cache.prepare(manifest)) {
                            runOnUiThread {
                                if (hasActiveAdminSession()) return@runOnUiThread
                                val sameManifest = PlaybackTransitionPolicy.sameManifest(
                                    activeManifest?.optString("id"),
                                    activeManifest?.optInt("version"),
                                    manifest.getString("id"),
                                    manifest.getInt("version"),
                                )
                                // Normal updates switch at the loop boundary. The first
                                // manifest and urgent updates activate immediately.
                                val urgent = manifest.optBoolean("urgent")
                                if (
                                    PlaybackTransitionPolicy.shouldActivateImmediately(
                                        hasActiveManifest = activeManifest != null,
                                        sameManifest = sameManifest,
                                        urgent = urgent,
                                    )
                                ) {
                                    if (activeManifest != null) {
                                        interruptCurrent("urgent_playlist_replacement")
                                    }
                                    val activated = cache.activateStaged()
                                    if (activated != null) {
                                        activeManifest = activated
                                        currentIndex = 0
                                        loopStartedAt = null
                                    } else {
                                        recordReplacementFailure(manifest, "activation")
                                        continuePreviousPlaylistAfterReplacementFailure()
                                        return@runOnUiThread
                                    }
                                }
                                markSuccessfulSync()
                                store.putState("device_mode", "play")
                                ensurePlaybackStarted()
                            }
                        } else {
                            recordReplacementFailure(manifest, "preparation")
                            runOnUiThread {
                                if (!hasActiveAdminSession()) {
                                    continuePreviousPlaylistAfterReplacementFailure()
                                }
                            }
                        }
                    }
                }
            } catch (_: Exception) {
                runOnUiThread {
                    if (hasActiveAdminSession()) return@runOnUiThread
                    if (!hasExternalPower()) {
                        showStatus(getString(R.string.maintenance))
                    } else {
                        when (store.state("device_mode")) {
                            "maintenance" -> showStatus(getString(R.string.maintenance))
                            "fallback" -> showFallback()
                            else -> {
                                activeManifest = cache.activeManifest()
                                ensurePlaybackStarted()
                                if (activeManifest == null) showFallback()
                            }
                        }
                    }
                }
            }
        }
    }

    private fun recordReplacementFailure(manifest: JSONObject, stage: String) {
        store.enqueueOperationalEvent(
            JSONObject()
                .put("kind", "replacement_failed")
                .put("recorded_at", serverClock.now().toString())
                .put(
                    "details",
                    JSONObject()
                        .put("playlist_id", manifest.getString("id"))
                        .put("stage", stage),
                ),
        )
    }

    private fun continuePreviousPlaylistAfterReplacementFailure() {
        activeManifest = cache.activeManifest() ?: activeManifest
        if (activeManifest == null) {
            store.putState("device_mode", "fallback")
            showFallback()
            return
        }
        store.putState("device_mode", "play")
        ensurePlaybackStarted()
    }

    private fun ensurePlaybackStarted() {
        if (playbackRecoveryBlocked) return
        val storedMode = store.state("device_mode")
        val effectiveMode = storedMode ?: if (activeManifest != null) "play" else null
        if (
            PlaybackTransitionPolicy.shouldStart(
                hasExternalPower = hasExternalPower(),
                mode = effectiveMode,
                hasActiveManifest = activeManifest != null,
                playbackActive = currentResultId != null,
                adminSessionActive = hasActiveAdminSession(),
            )
        ) {
            playCurrent()
        }
    }

    private fun playCurrent() {
        if (
            playbackRecoveryBlocked ||
            !hasExternalPower() ||
            hasActiveAdminSession() ||
            currentResultId != null
        ) return
        var manifest = activeManifest ?: return showFallback()
        var items = manifest.getJSONArray("items")
        if (items.length() == 0) return showFallback()
        if (currentIndex !in 0 until items.length()) {
            finishLoop(manifest)
            activeManifest = cache.activateStaged() ?: activeManifest
            currentIndex = 0
            manifest = activeManifest ?: return showFallback()
            items = manifest.getJSONArray("items")
            if (items.length() == 0) return showFallback()
        }
        if (loopStartedAt == null) {
            loopStartedAt = serverClock.now()
            store.putState("loop_started_at", loopStartedAt?.toString() ?: "")
        }
        val item = items.getJSONObject(currentIndex)
        val file = cache.validatedMediaFile(item)
        stopPlayback()
        currentStartedAt = serverClock.now()
        currentStartedElapsedMs = SystemClock.elapsedRealtime()
        currentStartedBootCount = serverClock.currentBootCount()
        currentResultId = UUID.randomUUID().toString()
        val playbackId = requireNotNull(currentResultId)
        store.putState(
            "current_playback",
            JSONObject()
                .put("result_id", playbackId)
                .put("playlist_id", manifest.getString("id"))
                .put("playlist_item_id", item.getString("entry_id"))
                .put("started_at", currentStartedAt.toString())
                .put("started_elapsed_ms", currentStartedElapsedMs)
                .put("started_boot_count", currentStartedBootCount)
                .put("item_index", currentIndex)
                .toString(),
        )
        binding.adminUnlock.visibility = View.GONE
        binding.adminControls.visibility = View.GONE
        binding.enrollment.visibility = View.GONE
        binding.status.visibility = View.GONE
        if (file == null) {
            if (recordCurrent("failed", "missing_file", 0)) advance()
            return
        }
        if (item.getString("kind") == "image") {
            binding.video.visibility = View.GONE
            val bitmap = BitmapFactory.decodeFile(file.path)
            if (bitmap == null) {
                if (recordCurrent("failed", "decode_failure", 0)) advance()
                return
            }
            binding.image.setImageBitmap(bitmap)
            binding.image.visibility = View.VISIBLE
            playbackHandler.postDelayed({
                if (
                    currentResultId == playbackId &&
                    recordCurrent("completed", "", item.getLong("duration_ms"))
                ) {
                    advance()
                }
            }, item.getLong("duration_ms"))
        } else {
            binding.image.visibility = View.GONE
            binding.video.visibility = View.VISIBLE
            binding.video.setVideoPath(file.path)
            binding.video.setOnCompletionListener {
                if (
                    currentResultId == playbackId &&
                    recordCurrent("completed", "", item.getLong("duration_ms"))
                ) {
                    advance()
                }
            }
            binding.video.setOnErrorListener { _, _, _ ->
                if (currentResultId == playbackId) {
                    val elapsed = elapsedMs()
                    if (recordCurrent("failed", "decode_failure", elapsed)) advance()
                }
                true
            }
            binding.video.start()
        }
    }

    private fun advance() {
        currentIndex += 1
        playCurrent()
    }

    private fun recordCurrent(status: String, reason: String, durationMs: Long): Boolean {
        val manifest = activeManifest ?: return false
        val items = manifest.getJSONArray("items")
        if (currentIndex !in 0 until items.length()) return false
        val resultId = currentResultId ?: return false
        val startedAt = currentStartedAt ?: return false
        val item = items.getJSONObject(currentIndex)
        val result = PlaybackResult(
            id = resultId,
            playlistItemId = item.getString("entry_id"),
            startedAt = startedAt.toString(),
            endedAt = serverClock.now().toString(),
            durationMs = durationMs,
            status = status,
            failureReason = reason,
        )
        currentResultId = null
        currentStartedAt = null
        currentStartedElapsedMs = null
        currentStartedBootCount = null
        loopResults += result
        persistLoopResults()
        store.putState("current_playback", "")
        store.putState("last_playback_at", result.endedAt ?: result.startedAt)
        return true
    }

    private fun interruptCurrent(reason: String) {
        if (currentResultId == null || currentStartedAt == null) return
        val elapsed = elapsedMs()
        stopPlayback()
        if (recordCurrent("interrupted", reason, elapsed)) {
            activeManifest?.let { finishLoop(it) }
        }
    }

    private fun finishLoop(manifest: JSONObject) {
        if (loopResults.isNotEmpty()) {
            val endedAt = serverClock.now()
            val batch = buildLoopBatch(
                manifest = manifest,
                sourceResults = loopResults,
                startedAt = loopStartedAt ?: serverClock.now(),
                endedAt = endedAt,
                capturedOffline = !isOnline(),
            )
            store.enqueueBatchAndClearPlaybackState(
                batch = batch,
                maxBytes = manifest.optLong(
                    "event_queue_bytes",
                    500L * 1024 * 1024,
                ),
                minimumFreeBytes = manifest.optLong(
                    "minimum_free_bytes",
                    2L * 1024 * 1024 * 1024,
                ),
                recordedAt = endedAt.toString(),
            )
        } else {
            store.clearPlaybackState()
        }
        loopResults.clear()
        loopStartedAt = null
        executor.execute { flushPendingBatches() }
    }

    private fun buildLoopBatch(
        manifest: JSONObject,
        sourceResults: List<PlaybackResult>,
        startedAt: Instant,
        endedAt: Instant,
        capturedOffline: Boolean,
    ): JSONObject {
        val items = manifest.getJSONArray("items")
        val completeResults = sourceResults.toMutableList()
        val recordedItems = completeResults.map { it.playlistItemId }.toMutableSet()
        for (index in 0 until items.length()) {
            val item = items.getJSONObject(index)
            val entryId = item.getString("entry_id")
            if (!recordedItems.contains(entryId)) {
                completeResults += PlaybackResult(
                    id = UUID.randomUUID().toString(),
                    playlistItemId = entryId,
                    startedAt = serverClock.now().toString(),
                    endedAt = serverClock.now().toString(),
                    durationMs = 0,
                    status = "interrupted",
                    failureReason = "loop_interrupted_before_entry",
                )
                recordedItems.add(entryId)
            }
        }
        val events = JSONArray()
        completeResults.forEach { result ->
            events.put(playbackResultToJson(result))
        }
        return JSONObject()
            .put("id", UUID.randomUUID().toString())
            .put("playlist_id", manifest.getString("id"))
            .put("loop_started_at", startedAt.toString())
            .put("loop_ended_at", endedAt.toString())
            .put("captured_offline", capturedOffline)
            .put("events", events)
    }

    private fun playbackResultToJson(result: PlaybackResult): JSONObject =
        JSONObject()
            .put("id", result.id)
            .put("playlist_item_id", result.playlistItemId)
            .put("started_at", result.startedAt)
            .put("ended_at", result.endedAt ?: JSONObject.NULL)
            .put("duration_ms", result.durationMs)
            .put("status", result.status)
            .put("failure_reason", result.failureReason)

    private fun persistLoopResults() {
        val events = JSONArray()
        loopResults.forEach { result -> events.put(playbackResultToJson(result)) }
        store.putState("loop_results", events.toString())
    }

    private fun persistedLoopResults(): MutableList<PlaybackResult> {
        val raw = store.state("loop_results")
        if (raw.isNullOrBlank()) return mutableListOf()
        val events = JSONArray(raw)
        val results = mutableListOf<PlaybackResult>()
        for (index in 0 until events.length()) {
            val event = events.getJSONObject(index)
            val endedAt = event.optString("ended_at").takeIf {
                it.isNotBlank() && it != "null"
            }
            results += PlaybackResult(
                id = event.getString("id"),
                playlistItemId = event.getString("playlist_item_id"),
                startedAt = event.getString("started_at"),
                endedAt = endedAt,
                durationMs = event.optLong("duration_ms", 0),
                status = event.getString("status"),
                failureReason = event.optString("failure_reason", ""),
            )
        }
        return results
    }

    private fun flushPendingBatches() {
        store.pendingOperationalEvents().forEach { (id, payload) ->
            try {
                api.uploadOperationalEvent(payload)
                store.acknowledgeOperationalEvent(id)
            } catch (_: Exception) {
                return
            }
        }
        store.pendingBatches().forEach { (id, payload) ->
            try {
                api.uploadBatch(payload)
                store.acknowledgeBatch(id)
            } catch (_: Exception) {
                return
            }
        }
    }

    private fun sendHeartbeat() {
        executor.execute {
            val battery = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            val level = battery?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
            val scale = battery?.getIntExtra(BatteryManager.EXTRA_SCALE, 100) ?: 100
            val temperatureTenths =
                battery?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0) ?: 0
            val batteryPercent = if (level != null && level >= 0) level * 100 / scale else null
            val body = JSONObject()
                .put("recorded_at", serverClock.now().toString())
                .put("screen_on", getSystemService(android.os.PowerManager::class.java).isInteractive)
                .put("external_power", hasExternalPower())
                .put("charging", hasExternalPower())
                .put("battery_percent", batteryPercent)
                .put(
                    "temperature_celsius",
                    if (temperatureTenths > 0) temperatureTenths / 10.0 else JSONObject.NULL,
                )
                .put("free_storage_bytes", filesDir.usableSpace)
                .put("app_version", BuildConfig.VERSION_NAME)
                .put("android_version", android.os.Build.VERSION.RELEASE)
                .put("active_playlist_id", activeManifest?.optString("id"))
                .put("playback_active", currentStartedAt != null)
                .put(
                    "last_successful_sync_at",
                    store.state("last_successful_sync_at"),
                )
                .put("last_playback_at", store.state("last_playback_at"))
            try {
                api.heartbeat(body)
            } catch (_: Exception) {
                // Health is best effort; playback and proof batches remain local.
            }
        }
    }

    private fun scheduleOperations() {
        operationsHandler.removeCallbacksAndMessages(null)
        val heartbeat = object : Runnable {
            override fun run() {
                sendHeartbeat()
                operationsHandler.postDelayed(this, 30 * 60 * 1000L)
            }
        }
        val sync = object : Runnable {
            override fun run() {
                synchronizeAndPlay()
                operationsHandler.postDelayed(this, 60 * 60 * 1000L)
            }
        }
        val midnightSync = object : Runnable {
            override fun run() {
                synchronizeAndPlay()
                operationsHandler.postDelayed(this, 24 * 60 * 60 * 1000L)
            }
        }
        operationsHandler.post(heartbeat)
        operationsHandler.post(sync)
        val now = serverClock.now().atZone(ZoneId.of("Asia/Kuala_Lumpur"))
        val nextMidnight = now.toLocalDate().plusDays(1).atStartOfDay(now.zone)
        operationsHandler.postDelayed(
            midnightSync,
            Duration.between(now, nextMidnight).toMillis(),
        )
    }

    private fun markSuccessfulSync() {
        store.putState("last_successful_sync_at", serverClock.now().toString())
    }

    private fun hasExternalPower(): Boolean {
        val battery = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val plugged = battery?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0
        return plugged != 0
    }

    private fun updateKeepScreenOn(externalPower: Boolean = hasExternalPower()) {
        if (ExternalPowerPolicy.shouldKeepScreenAwake(externalPower)) {
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        } else {
            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }
    }

    private fun elapsedMs(): Long =
        currentStartedElapsedMs?.let {
            (SystemClock.elapsedRealtime() - it).coerceAtLeast(0)
        } ?: 0

    private fun stopPlayback() {
        playbackHandler.removeCallbacksAndMessages(null)
        binding.video.setOnCompletionListener(null)
        binding.video.setOnErrorListener(null)
        binding.video.stopPlayback()
        binding.video.visibility = View.GONE
        binding.image.visibility = View.GONE
    }

    private fun showStatus(message: String) {
        stopPlayback()
        binding.enrollment.visibility = View.GONE
        binding.status.text = message
        binding.status.visibility = View.VISIBLE
    }

    private fun showFallback() {
        stopPlayback()
        binding.enrollment.visibility = View.GONE
        binding.status.visibility = View.GONE
        binding.image.setImageResource(R.drawable.dudu_fallback)
        binding.image.visibility = View.VISIBLE
    }

    private fun hideSystemUi() {
        window.insetsController?.let { controller ->
            controller.systemBarsBehavior =
                WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            controller.hide(WindowInsets.Type.systemBars())
        }
    }

    private fun enterLockedKiosk(): Boolean {
        if (!kioskPolicies.isDeviceOwner()) {
            if (!BuildConfig.IS_PRODUCTION) hideSystemUi()
            return !BuildConfig.IS_PRODUCTION
        }
        if (!kioskPolicies.applyLockedPolicies()) return false
        hideSystemUi()
        val activityManager = getSystemService(ActivityManager::class.java)
        if (activityManager.lockTaskModeState != ActivityManager.LOCK_TASK_MODE_NONE) {
            return true
        }
        return try {
            startLockTask()
            true
        } catch (_: IllegalStateException) {
            false
        } catch (_: SecurityException) {
            false
        }
    }

    private fun recoverInterruptedPlayback(): Boolean {
        val rawCheckpoint = store.state("current_playback")
        val rawLoopResults = store.state("loop_results")
        if (rawCheckpoint.isNullOrBlank() && rawLoopResults.isNullOrBlank()) return true
        val recovery = try {
            val manifest = activeManifest
                ?: throw IllegalStateException("active_manifest_missing")
            val items = manifest.getJSONArray("items")
            if (items.length() == 0) {
                throw IllegalStateException("active_manifest_empty")
            }
            val manifestEntryIds = buildList {
                for (index in 0 until items.length()) {
                    add(items.getJSONObject(index).getString("entry_id"))
                }
            }
            val endedAt = serverClock.now()
            loopResults.clear()
            loopResults.addAll(persistedLoopResults())
            validatePersistedLoopResults(loopResults, manifestEntryIds)

            var checkpointIndex: Int? = null
            var recoveryStartedAt = loopResults.firstOrNull()?.let {
                Instant.parse(it.startedAt)
            }
            if (!rawCheckpoint.isNullOrBlank()) {
                val previous = JSONObject(rawCheckpoint)
                if (previous.getString("playlist_id") != manifest.getString("id")) {
                    throw IllegalStateException("active_manifest_changed")
                }
                val restoredIndex = previous.getInt("item_index")
                if (restoredIndex !in 0 until items.length()) {
                    throw IllegalStateException("item_index_invalid")
                }
                val interruptedItem = previous.getString("playlist_item_id")
                if (manifestEntryIds[restoredIndex] != interruptedItem) {
                    throw IllegalStateException("playlist_entry_changed")
                }
                val resultId = previous.getString("result_id")
                UUID.fromString(resultId)
                val startedAt = Instant.parse(previous.getString("started_at"))
                recoveryStartedAt = recoveryStartedAt ?: startedAt
                val alreadyRecorded = loopResults.firstOrNull {
                    it.playlistItemId == interruptedItem
                }
                if (alreadyRecorded != null && alreadyRecorded.id != resultId) {
                    throw IllegalStateException("checkpoint_result_mismatch")
                }
                if (alreadyRecorded == null) {
                    loopResults += PlaybackResult(
                        id = resultId,
                        playlistItemId = interruptedItem,
                        startedAt = startedAt.toString(),
                        endedAt = endedAt.toString(),
                        durationMs = PlaybackRecoveryPolicy.recoveredInterruptionDurationMs(
                            startedElapsedRealtimeMs = previous.optLong(
                                "started_elapsed_ms",
                                -1,
                            ),
                            startedBootCount = previous.optInt("started_boot_count", -1),
                            currentElapsedRealtimeMs = SystemClock.elapsedRealtime(),
                            currentBootCount = serverClock.currentBootCount(),
                        ),
                        status = "interrupted",
                        failureReason = "app_restart_or_power_loss",
                    )
                }
                checkpointIndex = restoredIndex
            } else if (loopResults.isEmpty()) {
                // An empty serialized list contains no evidence to recover.
                store.clearPlaybackState()
                resetRecoveredPlaybackMemory()
                return true
            }

            val resumeIndex = PlaybackRecoveryPolicy.resumeIndex(
                manifestEntryIds = manifestEntryIds,
                recordedEntryIds = loopResults.map { it.playlistItemId },
                checkpointIndex = checkpointIndex,
            )
            val restoredLoopStartedAt = store.state("loop_started_at")
                ?.takeIf { it.isNotBlank() }
                ?.let { Instant.parse(it) }
                ?: recoveryStartedAt
                ?: throw IllegalStateException("loop_started_at_missing")
            val batch = buildLoopBatch(
                manifest = manifest,
                sourceResults = loopResults,
                startedAt = restoredLoopStartedAt,
                endedAt = endedAt,
                capturedOffline = true,
            )
            RecoveredPlayback(
                manifest = manifest,
                batch = batch,
                itemIndex = resumeIndex,
                recordedAt = endedAt,
            )
        } catch (error: Exception) {
            store.recordCheckpointLossAndClear(
                JSONObject()
                    .put("kind", "forced_queue_loss")
                    .put("recorded_at", serverClock.now().toString())
                    .put(
                        "details",
                        JSONObject().put(
                            "source",
                            "playback_checkpoint_recovery",
                        ).put(
                            "reason",
                            error.message?.takeIf { it in recoveryFailureReasons }
                                ?: "invalid_checkpoint",
                        ),
                    ),
            )
            currentIndex = 0
            resetRecoveredPlaybackMemory()
            return true
        }

        return try {
            store.enqueueBatchAndClearPlaybackState(
                batch = recovery.batch,
                maxBytes = recovery.manifest.optLong(
                    "event_queue_bytes",
                    500L * 1024 * 1024,
                ),
                minimumFreeBytes = recovery.manifest.optLong(
                    "minimum_free_bytes",
                    2L * 1024 * 1024 * 1024,
                ),
                recordedAt = recovery.recordedAt.toString(),
            )
            currentIndex = recovery.itemIndex
            true
        } catch (_: Exception) {
            // Do not start new playback until a restart confirms whether the
            // atomic queue/state transaction committed successfully.
            currentIndex = 0
            false
        } finally {
            resetRecoveredPlaybackMemory()
        }
    }

    private fun resetRecoveredPlaybackMemory() {
        currentResultId = null
        currentStartedAt = null
        currentStartedElapsedMs = null
        currentStartedBootCount = null
        loopResults.clear()
        loopStartedAt = null
    }

    private fun validatePersistedLoopResults(
        results: List<PlaybackResult>,
        manifestEntryIds: List<String>,
    ) {
        val seenEntries = mutableSetOf<String>()
        results.forEach { result ->
            UUID.fromString(result.id)
            Instant.parse(result.startedAt)
            result.endedAt?.let { Instant.parse(it) }
                ?: throw IllegalStateException("loop_result_incomplete")
            if (result.playlistItemId !in manifestEntryIds) {
                throw IllegalStateException("playlist_result_unknown")
            }
            if (!seenEntries.add(result.playlistItemId)) {
                throw IllegalStateException("playlist_result_duplicate")
            }
            if (result.durationMs < 0 || result.status !in playbackResultStatuses) {
                throw IllegalStateException("loop_result_invalid")
            }
        }
    }

    private fun isOnline(): Boolean {
        val connectivity = getSystemService(ConnectivityManager::class.java)
        val network = connectivity.activeNetwork ?: return false
        val capabilities = connectivity.getNetworkCapabilities(network) ?: return false
        return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }

    companion object {
        private val recoveryFailureReasons = setOf(
            "active_manifest_missing",
            "active_manifest_empty",
            "active_manifest_changed",
            "item_index_invalid",
            "playlist_entry_changed",
            "checkpoint_result_mismatch",
            "loop_started_at_missing",
            "loop_result_incomplete",
            "playlist_result_unknown",
            "playlist_result_duplicate",
            "loop_result_invalid",
        )
        private val playbackResultStatuses = setOf("completed", "interrupted", "failed")
    }

    private data class RecoveredPlayback(
        val manifest: JSONObject,
        val batch: JSONObject,
        val itemIndex: Int,
        val recordedAt: Instant,
    )
}
