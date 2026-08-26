package com.duducar.signage

import android.app.Activity
import android.app.ActivityManager
import android.app.AlertDialog
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
import java.time.Instant
import java.util.UUID
import java.util.concurrent.Executor
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

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
    private lateinit var operationsScheduler: OperationsScheduler
    private lateinit var appUpdater: AppUpdater
    private lateinit var uploadApi: ApiClient
    private val controlExecutor = Executors.newSingleThreadExecutor()
    private val downloadExecutor = Executors.newSingleThreadExecutor()
    private val uploadExecutor = Executors.newSingleThreadExecutor()
    private val syncInFlight = AtomicBoolean(false)
    private val syncPending = AtomicBoolean(false)
    private val heartbeatInFlight = AtomicBoolean(false)
    private val heartbeatPending = AtomicBoolean(false)
    private val uploadInFlight = AtomicBoolean(false)
    private val uploadPending = AtomicBoolean(false)
    private val manifestGeneration = AtomicLong(0)
    private val manifestPreparationLock = Any()
    private var pendingManifestPreparation: ManifestPreparation? = null
    private var manifestPreparationRunning = false
    private val playbackHandler = Handler(Looper.getMainLooper())
    private val operationsHandler = Handler(Looper.getMainLooper())
    private val adminHandler = Handler(Looper.getMainLooper())
    private var activeManifest: JSONObject? = null
    private var stagedManifestIdentity: ManifestIdentity? = null
    private var stagedManifestGeneration: Long? = null
    private var currentIndex = 0
    private var currentStartedAt: Instant? = null
    private var currentStartedElapsedMs: Long? = null
    private var currentStartedBootCount: Int? = null
    private var currentResultId: String? = null
    private var visiblePlaybackMedia = false
    private val loopResults = mutableListOf<PlaybackResult>()
    private var loopStartedAt: Instant? = null
    private var shutdownReceiverRegistered = false
    private var activityResumed = false
    private var resumeAfterAdminRelock = false
    private var playbackRecoveryBlocked = false
    private var shutdownPrepared = false

    private val adminRelock = Runnable { endAdminSession(bringToFront = true) }

    private val shutdownReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action == Intent.ACTION_SHUTDOWN) {
                // Android allows only a very short receiver window during
                // shutdown. This performs a local marker update only; it must
                // never start a request or playback from this broadcast.
                // ApplicationExitInfo timestamps use Android's wall-clock
                // coordinate, so local shutdown markers use that same clock.
                // API event timestamps remain server-corrected elsewhere.
                store.markPlannedShutdownOrderly(System.currentTimeMillis())
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
        operationsScheduler = OperationsScheduler(this)
        appUpdater = AppUpdater(this)
        api = ApiClient(credentials)
        uploadApi = ApiClient(credentials)
        cache = CacheManager(this)
        store = PlayerStore(this)
        serverClock = ServerClock(this)
        integrity = IntegrityClient(this)
        activeManifest = cache.activeManifest()
        shutdownPrepared = store.hasPlannedShutdownMarker()
        val checkpointRecovered = recoverInterruptedPlayback()
        playbackRecoveryBlocked = !checkpointRecovered
        configureAdminControls()
        configureShutdownControls()
        registerShutdownReceiver()
        updateKeepScreenOn()

        if (!checkpointRecovered) {
            credentials.endAdminSession()
            enterLockedKiosk()
            showStatus(getString(R.string.playback_recovery_failed))
            return
        }

        if (isShutdownPrepared()) {
            credentials.endAdminSession()
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
            showShutdownReady()
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

    private fun configureShutdownControls() {
        binding.prepareShutdownButton.setOnClickListener {
            requestShutdownPreparation()
        }
        binding.resumeDuduButton.setOnClickListener {
            requestResumeAfterShutdown()
        }
    }

    private fun registerShutdownReceiver() {
        val filter = IntentFilter(Intent.ACTION_SHUTDOWN)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(shutdownReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            registerReceiver(shutdownReceiver, filter)
        }
        shutdownReceiverRegistered = true
    }

    private fun requestShutdownPreparation() {
        if (isShutdownPrepared() || hasActiveAdminSession()) return
        AlertDialog.Builder(this)
            .setTitle(R.string.prepare_shutdown_title)
            .setMessage(R.string.prepare_shutdown_message)
            .setNegativeButton(android.R.string.cancel, null)
            .setPositiveButton(R.string.prepare_shutdown_confirm) { _, _ ->
                prepareForShutdown()
            }
            .show()
    }

    private fun prepareForShutdown() {
        if (isShutdownPrepared()) {
            showShutdownReady()
            return
        }
        // Snapshot this before taking the event timestamp. If an anchor arrives
        // concurrently just after the snapshot, rebasing an already-safe value
        // is harmless; the reverse ordering could leave an unsafe local value
        // untracked forever.
        val hadTrustedServerAnchor = serverClock.hasTrustedAnchor()
        val recordedAt = serverClock.now()
        val marker = PlannedShutdownMarker(
            id = UUID.randomUUID().toString(),
            preparedAtEpochMs = System.currentTimeMillis(),
            requiresTrustedTimestampRebase =
                ShutdownPreparationPolicy.requiresTrustedTimestampRebase(
                    hadTrustedServerAnchor,
                ),
        )
        val event = JSONObject()
            .put("id", marker.id)
            .put("kind", "planned_shutdown")
            .put("recorded_at", recordedAt.toString())
            .put("details", JSONObject())
        try {
            val created = store.preparePlannedShutdown(marker, event)
            shutdownPrepared = true
            operationsHandler.removeCallbacksAndMessages(null)
            operationsScheduler.cancel()
            if (created) {
                interruptCurrent("planned_shutdown")
            } else {
                stopPlayback()
            }
            showShutdownReady()
        } catch (_: Exception) {
            showStatus(getString(R.string.shutdown_prepare_failed))
        }
    }

    private fun requestResumeAfterShutdown() {
        if (!isShutdownPrepared()) return
        AlertDialog.Builder(this)
            .setTitle(R.string.resume_dudu_title)
            .setMessage(R.string.resume_dudu_message)
            .setNegativeButton(android.R.string.cancel, null)
            .setPositiveButton(R.string.resume_dudu_confirm) { _, _ ->
                resumeAfterPreparedShutdown()
            }
            .show()
    }

    private fun resumeAfterPreparedShutdown() {
        store.clearPlannedShutdownMarker()
        shutdownPrepared = false
        binding.shutdownReady.visibility = View.GONE
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

    private fun resumeConfiguredMode() {
        if (!ShutdownPreparationPolicy.shouldResumeAutomatically(isShutdownPrepared())) {
            showShutdownReady()
            return
        }
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
        if (shutdownReceiverRegistered) unregisterReceiver(shutdownReceiver)
        playbackHandler.removeCallbacksAndMessages(null)
        operationsHandler.removeCallbacksAndMessages(null)
        adminHandler.removeCallbacksAndMessages(null)
        controlExecutor.shutdownNow()
        downloadExecutor.shutdownNow()
        uploadExecutor.shutdownNow()
        if (::appUpdater.isInitialized) appUpdater.close()
        super.onDestroy()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (!::credentials.isInitialized) return
        if (isShutdownPrepared()) {
            operationsScheduler.cancel()
            showShutdownReady()
            return
        }
        when (intent.action) {
            OperationsScheduler.ACTION_HEARTBEAT -> {
                operationsScheduler.scheduleHeartbeat()
                sendHeartbeat()
            }
            OperationsScheduler.ACTION_SYNC -> {
                operationsScheduler.scheduleSync()
                synchronizeAndPlay()
            }
        }
    }

    override fun onResume() {
        super.onResume()
        activityResumed = true
        if (!::credentials.isInitialized) return
        updateKeepScreenOn()
        if (isShutdownPrepared()) {
            credentials.endAdminSession()
            if (enterLockedKiosk()) showShutdownReady()
            return
        }
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
        if (isShutdownPrepared()) return
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
        binding.prepareShutdown.visibility = View.GONE
        binding.shutdownReady.visibility = View.GONE
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
            if (!kioskPolicies.applyLockedPolicies(
                    restrictUsbFileTransfer = KioskAdminPolicy.shouldRestrictUsbFileTransfer(
                        isEnrolled = credentials.hasRefreshToken(),
                        isProduction = BuildConfig.IS_PRODUCTION,
                    ),
                )
            ) {
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
        binding.status.visibility = View.GONE
        binding.shutdownReady.visibility = View.GONE
        showPrepareShutdownControl()
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
            controlExecutor.execute {
                try {
                    @Suppress("HardwareIds")
                    val androidId = Settings.Secure.getString(
                        contentResolver,
                        Settings.Secure.ANDROID_ID,
                    )
                    val response = if (
                        EnrollmentPolicy.requiresIntegrity(BuildConfig.IS_PRODUCTION)
                    ) {
                        val challenge = api.enrollmentChallenge(code, androidId)
                        val integrityToken = integrity.token(
                            BuildConfig.PLAY_INTEGRITY_PROJECT_NUMBER,
                            challenge.getString("request_hash"),
                        )
                        api.enroll(challenge.getString("challenge_id"), integrityToken)
                    } else {
                        api.enrollDevelopment(code, androidId)
                    }
                    serverClock.update(response.getString("server_time"))
                    store.rebaseUnanchoredPlannedShutdownEvents(serverClock.now().toString())
                    runOnUiThread {
                        if (isShutdownPrepared()) {
                            showShutdownReady()
                            return@runOnUiThread
                        }
                        if (!enterLockedKiosk()) {
                            showStatus(
                                if (BuildConfig.IS_PRODUCTION) {
                                    getString(R.string.device_owner_required)
                                } else {
                                    getString(R.string.kiosk_policy_failed)
                                },
                            )
                            return@runOnUiThread
                        }
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
        if (isShutdownPrepared()) {
            showShutdownReady()
            return
        }
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
        requestCoalesced(
            executor = controlExecutor,
            inFlight = syncInFlight,
            pending = syncPending,
        ) {
            if (!isShutdownPrepared()) {
                synchronizeInBackground()
            }
        }
    }

    private fun synchronizeInBackground() {
        try {
            val response = api.manifest()
            serverClock.update(response.getString("server_time"))
            // A shutdown prepared before this first trusted anchor is durable
            // locally, but must receive this trusted timestamp before upload.
            store.rebaseUnanchoredPlannedShutdownEvents(serverClock.now().toString())
            collectHistoricalExitDiagnostics()
            requestUploadFlush()
            when (response.getString("mode")) {
                "maintenance", "fallback" -> {
                    invalidateManifestPreparation()
                    runOnUiThread {
                        if (hasActiveAdminSession() || isShutdownPrepared()) return@runOnUiThread
                        stagedManifestIdentity = null
                        stagedManifestGeneration = null
                        markSuccessfulSync()
                        if (response.getString("mode") == "maintenance") {
                            store.putState("device_mode", "maintenance")
                            interruptCurrent("device_disabled")
                            showStatus(getString(R.string.maintenance))
                        } else {
                            store.putState("device_mode", "fallback")
                            interruptCurrent("fallback_mode")
                            showFallback()
                        }
                    }
                }
                "play" -> {
                    queueManifestPreparation(response.getJSONObject("playlist"))
                    requestAppUpdate(response.optJSONObject("app_update"))
                }
                else -> throw IllegalArgumentException("Unknown device mode")
            }
        } catch (_: CredentialRejectedException) {
            handleCredentialsRejected()
        } catch (_: ForbiddenException) {
            handleServerForbidden()
        } catch (_: Exception) {
            restoreAfterSyncFailure()
        }
    }

    private fun queueManifestPreparation(manifest: JSONObject) {
        val candidate = runCatching { JSONObject(manifest.toString()) }.getOrNull()
            ?: return restoreAfterManifestPreparationFailure(manifest)
        synchronized(manifestPreparationLock) {
            // A later sync invalidates any older candidate before its downloads can
            // finish, so a stale staged manifest cannot activate at a loop boundary.
            val nextGeneration = manifestGeneration.incrementAndGet()
            cache.discardStaged()
            pendingManifestPreparation = ManifestPreparation(nextGeneration, candidate)
            if (manifestPreparationRunning) return
            manifestPreparationRunning = true
        }
        downloadExecutor.execute {
            while (true) {
                val next = synchronized(manifestPreparationLock) {
                    pendingManifestPreparation?.also { pendingManifestPreparation = null }
                        ?: run {
                            manifestPreparationRunning = false
                            return@execute
                        }
                }
                val identity = cache.prepare(next.manifest) { identity, preparedManifest ->
                    synchronized(manifestPreparationLock) {
                        manifestGeneration.get() == next.generation &&
                            cache.stageCandidate(identity, preparedManifest)
                    }
                }
                val current = synchronized(manifestPreparationLock) {
                    manifestGeneration.get() == next.generation
                }
                if (identity != null && current) {
                    runOnUiThread { activatePreparedManifest(next.manifest, identity, next.generation) }
                } else if (current) {
                    restoreAfterManifestPreparationFailure(next.manifest)
                }
            }
        }
    }

    private fun requestAppUpdate(payload: JSONObject?) {
        val metadata = AppUpdatePolicy.parse(payload, BuildConfig.VERSION_CODE) ?: return
        runOnUiThread {
            val battery = currentBatteryState()
            if (!AppUpdatePolicy.mayStage(
                    isProduction = BuildConfig.IS_PRODUCTION,
                    isDeviceOwner = kioskPolicies.isDeviceOwner(),
                    shutdownPrepared = isShutdownPrepared(),
                    adminSessionActive = hasActiveAdminSession(),
                    usableBytes = filesDir.usableSpace,
                    updateSizeBytes = metadata.sizeBytes,
                    batteryPercent = battery.first,
                    charging = battery.second,
                )
            ) return@runOnUiThread
            appUpdater.stage(metadata) { apk ->
                val readyBattery = currentBatteryState()
                if (!AppUpdatePolicy.mayStage(
                        isProduction = BuildConfig.IS_PRODUCTION,
                        isDeviceOwner = kioskPolicies.isDeviceOwner(),
                        shutdownPrepared = isShutdownPrepared(),
                        adminSessionActive = hasActiveAdminSession(),
                        usableBytes = filesDir.usableSpace,
                        updateSizeBytes = metadata.sizeBytes,
                        batteryPercent = readyBattery.first,
                        charging = readyBattery.second,
                    )
                ) {
                    false
                } else {
                    interruptCurrent("app_update")
                    stopPlayback()
                    appUpdater.install(apk, metadata)
                }
            }
        }
    }

    private fun currentBatteryState(): Pair<Int?, Boolean?> {
        val intent = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val level = intent?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = intent?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
        val status = intent?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
        val percent = if (level >= 0 && scale > 0) level * 100 / scale else null
        val charging = when (status) {
            BatteryManager.BATTERY_STATUS_CHARGING,
            BatteryManager.BATTERY_STATUS_FULL,
            -> true
            BatteryManager.BATTERY_STATUS_DISCHARGING,
            BatteryManager.BATTERY_STATUS_NOT_CHARGING,
            -> false
            else -> null
        }
        return percent to charging
    }

    private fun invalidateManifestPreparation() {
        synchronized(manifestPreparationLock) {
            manifestGeneration.incrementAndGet()
            cache.discardStaged()
        }
    }

    private fun activatePreparedManifest(
        manifest: JSONObject,
        identity: ManifestIdentity,
        generation: Long,
    ) {
        if (hasActiveAdminSession() || isShutdownPrepared()) return
        val sameManifest = PlaybackTransitionPolicy.sameManifest(
            activeManifest?.optString("id"),
            activeManifest?.optInt("version"),
            identity.id,
            identity.version,
        )
        if (
            PlaybackTransitionPolicy.shouldActivateImmediately(
                hasActiveManifest = activeManifest != null,
                sameManifest = sameManifest,
                urgent = manifest.getBoolean("urgent"),
            )
        ) {
            val (attempted, activated) = synchronized(manifestPreparationLock) {
                if (generation != manifestGeneration.get()) {
                    false to null
                } else {
                    if (activeManifest != null) interruptCurrent("urgent_playlist_replacement")
                    true to cache.activateStaged(identity)
                }
            }
            if (!attempted) return
            if (activated == null) {
                stagedManifestIdentity = null
                stagedManifestGeneration = null
                recordReplacementFailure(manifest, "activation")
                continuePreviousPlaylistAfterReplacementFailure()
                return
            }
            activeManifest = activated
            stagedManifestIdentity = null
            stagedManifestGeneration = null
            currentIndex = 0
            loopStartedAt = null
        } else {
            val current = synchronized(manifestPreparationLock) {
                generation == manifestGeneration.get()
            }
            if (!current) return
            stagedManifestIdentity = identity
            stagedManifestGeneration = generation
        }
        markSuccessfulSync()
        store.putState("device_mode", "play")
        ensurePlaybackStarted()
    }

    private fun restoreAfterManifestPreparationFailure(manifest: JSONObject) {
        recordReplacementFailure(manifest, "preparation")
        runOnUiThread {
            if (!hasActiveAdminSession() && !isShutdownPrepared()) {
                continuePreviousPlaylistAfterReplacementFailure()
            }
        }
    }

    private fun restoreAfterSyncFailure() {
        runOnUiThread {
            if (hasActiveAdminSession() || isShutdownPrepared()) return@runOnUiThread
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

    private fun handleCredentialsRejected() {
        runOnUiThread {
            if (isShutdownPrepared()) {
                showShutdownReady()
                return@runOnUiThread
            }
            operationsHandler.removeCallbacksAndMessages(null)
            operationsScheduler.cancel()
            api.clearAccessToken()
            uploadApi.clearAccessToken()
            adminRelockScheduler.cancel()
            credentials.endAdminSession()
            invalidateManifestPreparation()
            stagedManifestIdentity = null
            stagedManifestGeneration = null
            interruptCurrent("credential_rejected")
            showEnrollment()
        }
    }

    private fun handleServerForbidden() {
        runOnUiThread {
            if (isShutdownPrepared()) {
                showShutdownReady()
                return@runOnUiThread
            }
            // A policy/disabled-device denial must fail closed: do not keep
            // advertising from cache while waiting for the next permitted sync.
            invalidateManifestPreparation()
            stagedManifestIdentity = null
            stagedManifestGeneration = null
            store.putState("device_mode", "maintenance")
            interruptCurrent("server_forbidden")
            showStatus(getString(R.string.maintenance))
        }
    }

    private fun recordReplacementFailure(manifest: JSONObject, stage: String) {
        val identity = ManifestPolicy.identity(manifest) ?: return
        recordReplacementFailure(identity, stage)
    }

    private fun recordReplacementFailure(identity: ManifestIdentity, stage: String) {
        store.enqueueOperationalEvent(
            JSONObject()
                .put("kind", "replacement_failed")
                .put("recorded_at", serverClock.now().toString())
                .put(
                    "details",
                    JSONObject()
                        .put("playlist_id", identity.id)
                        .put("stage", stage),
                ),
        )
    }

    private fun continuePreviousPlaylistAfterReplacementFailure() {
        if (isShutdownPrepared()) {
            showShutdownReady()
            return
        }
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
        if (playbackRecoveryBlocked || isShutdownPrepared()) return
        val storedMode = store.state("device_mode")
        val effectiveMode = storedMode ?: if (activeManifest != null) "play" else null
        if (
            PlaybackTransitionPolicy.shouldStart(
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
            isShutdownPrepared() ||
            hasActiveAdminSession() ||
            currentResultId != null
        ) return
        var manifest = activeManifest ?: return showFallback()
        var items = manifest.getJSONArray("items")
        if (items.length() == 0) return showFallback()
        if (currentIndex !in 0 until items.length()) {
            val priorLoopHadOnlyFailures =
                loopResults.isNotEmpty() && loopResults.all { it.status == "failed" }
            finishLoop(manifest)
            val pendingIdentity = stagedManifestIdentity
            val (stagedIsCurrent, activated) = synchronized(manifestPreparationLock) {
                val current =
                    pendingIdentity != null && stagedManifestGeneration == manifestGeneration.get()
                current to if (current) cache.activateStaged(pendingIdentity) else null
            }
            if (pendingIdentity != null) {
                if (stagedIsCurrent && activated == null) {
                    recordReplacementFailure(pendingIdentity, "activation")
                }
                stagedManifestIdentity = null
                stagedManifestGeneration = null
            }
            activeManifest = activated ?: activeManifest
            currentIndex = 0
            manifest = activeManifest ?: return showFallback()
            items = manifest.getJSONArray("items")
            if (items.length() == 0) return showFallback()
            if (priorLoopHadOnlyFailures && activated == null) {
                showFallback()
                playbackHandler.postDelayed({ ensurePlaybackStarted() }, INVALID_PLAYLIST_RETRY_MS)
                return
            }
        }
        if (loopStartedAt == null) {
            loopStartedAt = serverClock.now()
            store.putState("loop_started_at", loopStartedAt?.toString() ?: "")
        }
        while (currentIndex in 0 until items.length()) {
            val item = items.getJSONObject(currentIndex)
            val playbackId = beginCurrentPlayback(manifest, item)
            val file = cache.validatedMediaFile(item)
            if (file == null) {
                if (!recordFailureAndContinue(playbackId, "missing_file", 0)) return
                continue
            }
            if (item.getString("kind") == "image") {
                val bitmap = BitmapFactory.decodeFile(file.path)
                if (bitmap == null) {
                    if (!recordFailureAndContinue(playbackId, "decode_failure", 0)) return
                    continue
                }
                binding.video.visibility = View.GONE
                binding.image.setImageBitmap(bitmap)
                binding.image.visibility = View.VISIBLE
                visiblePlaybackMedia = true
                updateKeepScreenOn()
                playbackHandler.postDelayed({
                    if (
                        currentResultId == playbackId &&
                        recordCurrent("completed", "", item.getLong("duration_ms"))
                    ) {
                        advance()
                    }
                }, item.getLong("duration_ms"))
                return
            }
            startVideoPlayback(file.path, item.getLong("duration_ms"), playbackId)
            return
        }
        // Continue at the loop boundary on the next main-loop turn. A corrupt
        // playlist therefore records each failed item once without recursion.
        playbackHandler.post { playCurrent() }
    }

    private fun beginCurrentPlayback(manifest: JSONObject, item: JSONObject): String {
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
        binding.shutdownReady.visibility = View.GONE
        showPrepareShutdownControl()
        return playbackId
    }

    private fun recordFailureAndContinue(
        playbackId: String,
        reason: String,
        durationMs: Long,
    ): Boolean {
        if (currentResultId != playbackId || !recordCurrent("failed", reason, durationMs)) return false
        currentIndex += 1
        return true
    }

    private fun startVideoPlayback(path: String, durationMs: Long, playbackId: String) {
        binding.image.visibility = View.GONE
        binding.video.visibility = View.VISIBLE
        visiblePlaybackMedia = true
        updateKeepScreenOn()
        var prepared = false
        binding.video.setOnPreparedListener {
            if (currentResultId != playbackId) return@setOnPreparedListener
            prepared = true
            binding.video.start()
            playbackHandler.postDelayed({
                failVideoPlayback(playbackId, "playback_timeout")
            }, durationMs + VIDEO_COMPLETION_GRACE_MS)
        }
        binding.video.setOnCompletionListener {
            if (
                currentResultId == playbackId &&
                recordCurrent("completed", "", durationMs)
            ) {
                advance()
            }
        }
        binding.video.setOnErrorListener { _, _, _ ->
            failVideoPlayback(playbackId, "decode_failure")
            true
        }
        binding.video.setVideoPath(path)
        playbackHandler.postDelayed({
            if (currentResultId == playbackId && !prepared) {
                failVideoPlayback(playbackId, "start_timeout")
            }
        }, VIDEO_START_TIMEOUT_MS)
    }

    private fun failVideoPlayback(playbackId: String, reason: String) {
        if (currentResultId != playbackId) return
        val elapsed = elapsedMs()
        stopPlayback()
        if (recordCurrent("failed", reason, elapsed)) advance()
    }

    private fun advance() {
        currentIndex += 1
        playbackHandler.post { playCurrent() }
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
        visiblePlaybackMedia = false
        updateKeepScreenOn()
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
        requestUploadFlush()
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
        // Never send a queue headed by an event timestamped from an
        // unanchored device clock. This also covers a process death between a
        // successful clock update and the normal sync-path rebasing call.
        if (!serverClock.hasTrustedAnchor()) return
        store.rebaseUnanchoredPlannedShutdownEvents(serverClock.now().toString())
        while (true) {
            val (id, payload) = store.oldestPendingOperationalEvent() ?: break
            try {
                uploadApi.uploadOperationalEvent(payload)
                store.acknowledgeOperationalEvent(id)
            } catch (error: ApiException) {
                if (!isPermanentUploadRejection(error)) return
                store.discardOperationalEventAsPoison(id, error.status)
            } catch (_: CredentialRejectedException) {
                handleCredentialsRejected()
                return
            } catch (_: ForbiddenException) {
                handleServerForbidden()
                return
            } catch (_: Exception) {
                return
            }
        }
        while (true) {
            val (id, payload) = store.oldestPendingBatch(serverClock.now().toString()) ?: break
            try {
                uploadApi.uploadBatch(payload)
                store.acknowledgeBatch(id)
            } catch (error: ApiException) {
                if (!isPermanentUploadRejection(error)) return
                store.discardBatchAsPoison(id, error.status, serverClock.now().toString())
            } catch (_: CredentialRejectedException) {
                handleCredentialsRejected()
                return
            } catch (_: ForbiddenException) {
                handleServerForbidden()
                return
            } catch (_: Exception) {
                return
            }
        }
    }

    private fun requestUploadFlush() {
        requestCoalesced(uploadExecutor, uploadInFlight, uploadPending) { flushPendingBatches() }
    }

    private fun isPermanentUploadRejection(error: ApiException): Boolean =
        error.status in 400..499 && error.status !in setOf(401, 403, 408, 429)

    private fun sendHeartbeat() {
        if (isShutdownPrepared()) return
        requestCoalesced(controlExecutor, heartbeatInFlight, heartbeatPending) {
            sendHeartbeatOnce()
        }
    }

    private fun sendHeartbeatOnce() {
        if (isShutdownPrepared()) return
        try {
            val battery = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            val level = battery?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
            val scale = battery?.getIntExtra(BatteryManager.EXTRA_SCALE, 100)?.takeIf { it > 0 } ?: 100
            val temperatureTenths =
                battery?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0) ?: 0
            val batteryPercent = if (level != null && level >= 0) level * 100 / scale else null
            val body = JSONObject()
                .put("recorded_at", serverClock.now().toString())
                .put("screen_on", getSystemService(android.os.PowerManager::class.java).isInteractive)
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
                .put("last_successful_sync_at", store.state("last_successful_sync_at"))
                .put("last_playback_at", store.state("last_playback_at"))
            api.heartbeat(body)
        } catch (_: CredentialRejectedException) {
            handleCredentialsRejected()
        } catch (_: ForbiddenException) {
            handleServerForbidden()
        } catch (_: Exception) {
            // Health is best effort; playback and proof batches remain local.
        }
    }

    private fun requestCoalesced(
        executor: Executor,
        inFlight: AtomicBoolean,
        pending: AtomicBoolean,
        work: () -> Unit,
    ) {
        pending.set(true)
        if (!inFlight.compareAndSet(false, true)) return
        executor.execute {
            try {
                while (pending.getAndSet(false)) work()
            } finally {
                inFlight.set(false)
                if (pending.get()) requestCoalesced(executor, inFlight, pending, work)
            }
        }
    }

    private fun scheduleOperations() {
        operationsHandler.removeCallbacksAndMessages(null)
        if (isShutdownPrepared()) return
        operationsScheduler.scheduleHeartbeat()
        operationsScheduler.scheduleSync()
        val heartbeat = object : Runnable {
            override fun run() {
                if (isShutdownPrepared()) return
                sendHeartbeat()
                if (!isShutdownPrepared()) {
                    operationsHandler.postDelayed(this, OperationsScheduler.HEARTBEAT_INTERVAL_MS)
                }
            }
        }
        val sync = object : Runnable {
            override fun run() {
                if (isShutdownPrepared()) return
                synchronizeAndPlay()
                if (!isShutdownPrepared()) {
                    operationsHandler.postDelayed(this, OperationsScheduler.SYNC_INTERVAL_MS)
                }
            }
        }
        operationsHandler.post(heartbeat)
        operationsHandler.post(sync)
    }

    private fun markSuccessfulSync() {
        store.putState("last_successful_sync_at", serverClock.now().toString())
    }

    private fun updateKeepScreenOn() {
        if (
            ScreenAwakePolicy.shouldKeepScreenAwake(
                playbackActive = currentResultId != null && !isShutdownPrepared(),
                visibleMedia = visiblePlaybackMedia,
            )
        ) {
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
        visiblePlaybackMedia = false
        updateKeepScreenOn()
    }

    private fun showStatus(message: String) {
        stopPlayback()
        binding.enrollment.visibility = View.GONE
        binding.shutdownReady.visibility = View.GONE
        binding.status.text = message
        binding.status.visibility = View.VISIBLE
        showPrepareShutdownControl()
    }

    private fun showFallback() {
        stopPlayback()
        binding.enrollment.visibility = View.GONE
        binding.shutdownReady.visibility = View.GONE
        binding.status.visibility = View.GONE
        binding.image.setImageResource(R.drawable.dudu_fallback)
        binding.image.visibility = View.VISIBLE
        updateKeepScreenOn()
        showPrepareShutdownControl()
    }

    private fun showShutdownReady() {
        // Covers a process death in the tiny interval after the durable marker
        // was written but before the normal prepare path cancelled its alarms.
        operationsScheduler.cancel()
        stopPlayback()
        binding.adminUnlock.visibility = View.GONE
        binding.adminControls.visibility = View.GONE
        binding.enrollment.visibility = View.GONE
        binding.status.visibility = View.GONE
        binding.prepareShutdown.visibility = View.GONE
        binding.shutdownReady.visibility = View.VISIBLE
        hideSystemUi()
    }

    private fun showPrepareShutdownControl() {
        binding.prepareShutdown.visibility =
            if (!isShutdownPrepared() && !hasActiveAdminSession()) View.VISIBLE else View.GONE
    }

    private fun isShutdownPrepared(): Boolean =
        shutdownPrepared || store.hasPlannedShutdownMarker()

    private fun collectHistoricalExitDiagnostics() {
        if (
            Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
                !ExitHistoryPolicy.shouldCollectDiagnostics(serverClock.hasTrustedAnchor())
        ) {
            return
        }
        val history = try {
            getSystemService(ActivityManager::class.java)
                .getHistoricalProcessExitReasons(packageName, 0, 0)
        } catch (_: Exception) {
            return
        }
        val entries = history.map { info ->
            ExitHistoryEntry(
                timestampMs = info.timestamp,
                androidReason = info.reason,
            )
        }
        val installationIdentity = store.exitHistoryInstallationIdentity()
        val cursor = store.exitHistoryCursor()
        val unprocessedEntries = ExitHistoryPolicy.unprocessedEntries(
            installationIdentity = installationIdentity,
            entries = entries,
            cursor = cursor,
        )
        if (unprocessedEntries.isEmpty()) return
        val now = serverClock.now()
        val localNowEpochMs = System.currentTimeMillis()
        val shutdownMarkers = listOfNotNull(
            store.plannedShutdownMarker(),
            // This is deliberately separate from the active marker. A
            // confirmed Resume DUDU removes the stopped-state gate but must
            // retain the orderly observation long enough to classify the
            // preceding process exit correctly.
            store.recentOrderlyPlannedShutdownMarker(localNowEpochMs),
        )
        val events = unprocessedEntries.mapNotNull { entry ->
            val reason = ExitHistoryPolicy.abnormalReason(
                androidReason = entry.androidReason,
                supportsFreezerTermination = true,
            ) ?: return@mapNotNull null
            if (shutdownMarkers.any { marker ->
                    ShutdownPreparationPolicy.shouldSuppressAbnormalExit(
                        marker = marker,
                        exitTimestampMs = entry.timestampMs,
                        nowEpochMs = localNowEpochMs,
                    )
                }
            ) {
                return@mapNotNull null
            }
            JSONObject()
                .put("id", ExitHistoryPolicy.stableEventId(installationIdentity, entry))
                .put("kind", "abnormal_app_exit")
                .put("recorded_at", now.toString())
                .put("details", JSONObject().put("reason", reason))
        }
        store.enqueueOperationalEventsAndAdvanceExitHistoryCursor(
            events = events,
            cursor = ExitHistoryPolicy.advanceCursor(
                installationIdentity = installationIdentity,
                cursor = cursor,
                entries = entries,
            ),
        )
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
        if (!kioskPolicies.applyLockedPolicies(
                restrictUsbFileTransfer = KioskAdminPolicy.shouldRestrictUsbFileTransfer(
                    isEnrolled = credentials.hasRefreshToken(),
                    isProduction = BuildConfig.IS_PRODUCTION,
                ),
            )
        ) return false
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
                        failureReason = ShutdownPreparationPolicy.recoveredInterruptionReason(
                            store.plannedShutdownMarker(),
                        ),
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
        } catch (_: Exception) {
            store.recordCheckpointLossAndClear(
                JSONObject()
                    .put("kind", "forced_queue_loss")
                    .put("recorded_at", serverClock.now().toString())
                    .put(
                        "details",
                        // The API's loss event represents discarded queued
                        // batches only. Checkpoint recovery loses no batch,
                        // but remains a durable, schema-valid loss signal.
                        JSONObject()
                            .put("removed_batches", 0)
                            .put("estimated_removed_bytes", 0)
                            .put("target_removed_bytes", 0),
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
        private const val VIDEO_START_TIMEOUT_MS = 10_000L
        private const val VIDEO_COMPLETION_GRACE_MS = 15_000L
        private const val INVALID_PLAYLIST_RETRY_MS = 60_000L
        private val playbackResultStatuses = setOf("completed", "interrupted", "failed")
    }

    private data class RecoveredPlayback(
        val manifest: JSONObject,
        val batch: JSONObject,
        val itemIndex: Int,
        val recordedAt: Instant,
    )

    private data class ManifestPreparation(
        val generation: Long,
        val manifest: JSONObject,
    )
}
