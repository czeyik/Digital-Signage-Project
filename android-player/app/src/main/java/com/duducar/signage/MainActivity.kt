package com.duducar.signage

import android.app.admin.DevicePolicyManager
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.BitmapFactory
import android.os.BatteryManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.View
import android.view.WindowManager
import android.app.Activity
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import com.duducar.signage.databinding.ActivityMainBinding
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.util.UUID
import java.util.concurrent.Executors

class MainActivity : Activity() {
    private lateinit var binding: ActivityMainBinding
    private lateinit var api: ApiClient
    private lateinit var cache: CacheManager
    private lateinit var store: PlayerStore
    private lateinit var serverClock: ServerClock
    private val executor = Executors.newSingleThreadExecutor()
    private val playbackHandler = Handler(Looper.getMainLooper())
    private val operationsHandler = Handler(Looper.getMainLooper())
    private var activeManifest: JSONObject? = null
    private var currentIndex = 0
    private var currentStartedAt: Instant? = null
    private var currentResultId: String? = null
    private val loopResults = mutableListOf<PlaybackResult>()
    private var loopStartedAt: Instant? = null

    private val powerReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action == Intent.ACTION_POWER_DISCONNECTED) {
                interruptCurrent("external_power_lost")
                stopPlayback()
                binding.status.text = getString(R.string.maintenance)
                binding.status.visibility = View.VISIBLE
            } else if (intent.action == Intent.ACTION_POWER_CONNECTED) {
                synchronizeAndPlay()
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        window.addFlags(
            WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or
                WindowManager.LayoutParams.FLAG_SECURE,
        )
        hideSystemUi()
        enterLockTaskIfManaged()

        val credentials = CredentialStore(this)
        api = ApiClient(credentials)
        cache = CacheManager(this)
        store = PlayerStore(this)
        serverClock = ServerClock(this)
        activeManifest = cache.activeManifest()
        recoverInterruptedPlayback()

        registerReceiver(
            powerReceiver,
            IntentFilter().apply {
                addAction(Intent.ACTION_POWER_CONNECTED)
                addAction(Intent.ACTION_POWER_DISCONNECTED)
            },
        )

        if (credentials.hasRefreshToken()) {
            if (store.state("device_mode") == "maintenance") {
                showStatus(getString(R.string.maintenance))
            } else {
                synchronizeAndPlay()
            }
            scheduleOperations()
        } else {
            showEnrollment()
        }
    }

    override fun onDestroy() {
        unregisterReceiver(powerReceiver)
        executor.shutdownNow()
        super.onDestroy()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemUi()
    }

    private fun showEnrollment() {
        binding.status.visibility = View.GONE
        binding.enrollment.visibility = View.VISIBLE
        binding.enrollButton.setOnClickListener {
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
                    val response = api.enroll(code, androidId)
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
        if (!hasExternalPower()) {
            binding.status.text = getString(R.string.maintenance)
            binding.status.visibility = View.VISIBLE
            return
        }
        executor.execute {
            try {
                flushPendingBatches()
                val response = api.manifest()
                serverClock.update(response.getString("server_time"))
                when (response.getString("mode")) {
                    "maintenance" -> runOnUiThread {
                        store.putState("device_mode", "maintenance")
                        showStatus(getString(R.string.maintenance))
                    }
                    "fallback" -> runOnUiThread {
                        store.putState("device_mode", "fallback")
                        showStatus(getString(R.string.fallback))
                    }
                    "play" -> {
                        val manifest = response.getJSONObject("playlist")
                        if (cache.prepare(manifest)) {
                            runOnUiThread {
                                store.putState("device_mode", "play")
                                val sameManifest =
                                    activeManifest?.optString("id") == manifest.optString("id") &&
                                        activeManifest?.optInt("version") == manifest.optInt("version")
                                // Normal updates switch at the loop boundary. The first
                                // manifest and urgent updates activate immediately.
                                val urgent = manifest.optBoolean("urgent")
                                if (urgent && activeManifest != null && !sameManifest) {
                                    interruptCurrent("urgent_playlist_replacement")
                                    activeManifest = cache.activateStaged()
                                    currentIndex = 0
                                    playCurrent()
                                } else if (activeManifest == null) {
                                    activeManifest = cache.activateStaged()
                                    currentIndex = 0
                                    playCurrent()
                                }
                            }
                        }
                    }
                }
            } catch (_: Exception) {
                runOnUiThread {
                    if (store.state("device_mode") == "maintenance") {
                        showStatus(getString(R.string.maintenance))
                        return@runOnUiThread
                    }
                    activeManifest = cache.activeManifest()
                    if (activeManifest != null) playCurrent() else showStatus(getString(R.string.fallback))
                }
            }
        }
    }

    private fun playCurrent() {
        if (!hasExternalPower()) return
        val manifest = activeManifest ?: return showStatus(getString(R.string.fallback))
        val items = manifest.getJSONArray("items")
        if (items.length() == 0) return showStatus(getString(R.string.fallback))
        if (loopStartedAt == null) {
            loopStartedAt = serverClock.now()
            store.putState("loop_started_at", loopStartedAt?.toString() ?: "")
        }
        if (currentIndex >= items.length()) {
            finishLoop(manifest)
            activeManifest = cache.activateStaged() ?: activeManifest
            currentIndex = 0
        }
        val item = activeManifest!!.getJSONArray("items").getJSONObject(currentIndex)
        val file = cache.mediaFile(item.getString("media_id"))
        currentStartedAt = serverClock.now()
        currentResultId = UUID.randomUUID().toString()
        store.putState(
            "current_playback",
            JSONObject()
                .put("result_id", currentResultId)
                .put("playlist_id", manifest.getString("id"))
                .put("playlist_item_id", item.getString("entry_id"))
                .put("started_at", currentStartedAt.toString())
                .put("item_index", currentIndex)
                .toString(),
        )
        binding.status.visibility = View.GONE
        if (!file.exists()) {
            recordCurrent("failed", "missing_file", 0)
            advance()
            return
        }
        if (item.getString("kind") == "image") {
            binding.video.visibility = View.GONE
            binding.image.setImageBitmap(BitmapFactory.decodeFile(file.path))
            binding.image.visibility = View.VISIBLE
            playbackHandler.postDelayed({
                recordCurrent("completed", "", item.getLong("duration_ms"))
                advance()
            }, item.getLong("duration_ms"))
        } else {
            binding.image.visibility = View.GONE
            binding.video.visibility = View.VISIBLE
            binding.video.setVideoPath(file.path)
            binding.video.setOnCompletionListener {
                recordCurrent("completed", "", item.getLong("duration_ms"))
                advance()
            }
            binding.video.setOnErrorListener { _, _, _ ->
                recordCurrent("failed", "decode_failure", elapsedMs())
                advance()
                true
            }
            binding.video.start()
        }
    }

    private fun advance() {
        currentStartedAt = null
        currentResultId = null
        currentIndex += 1
        playCurrent()
    }

    private fun recordCurrent(status: String, reason: String, durationMs: Long) {
        val manifest = activeManifest ?: return
        val items = manifest.getJSONArray("items")
        if (currentIndex >= items.length()) return
        val item = items.getJSONObject(currentIndex)
        val result = PlaybackResult(
            id = currentResultId ?: UUID.randomUUID().toString(),
            playlistItemId = item.getString("entry_id"),
            startedAt = (currentStartedAt ?: serverClock.now()).toString(),
            endedAt = serverClock.now().toString(),
            durationMs = durationMs,
            status = status,
            failureReason = reason,
        )
        loopResults += result
        persistLoopResults()
        store.putState("current_playback", "")
    }

    private fun interruptCurrent(reason: String) {
        if (currentStartedAt == null) return
        playbackHandler.removeCallbacksAndMessages(null)
        recordCurrent("interrupted", reason, elapsedMs())
        activeManifest?.let { finishLoop(it) }
    }

    private fun finishLoop(manifest: JSONObject) {
        if (loopResults.isEmpty()) return
        enqueueLoopBatch(
            manifest = manifest,
            sourceResults = loopResults,
            startedAt = loopStartedAt ?: serverClock.now(),
            endedAt = serverClock.now(),
            capturedOffline = !isOnline(),
        )
        loopResults.clear()
        store.putState("loop_results", "")
        loopStartedAt = serverClock.now()
        store.putState("loop_started_at", loopStartedAt?.toString() ?: "")
        executor.execute { flushPendingBatches() }
    }

    private fun enqueueLoopBatch(
        manifest: JSONObject,
        sourceResults: List<PlaybackResult>,
        startedAt: Instant,
        endedAt: Instant,
        capturedOffline: Boolean,
    ) {
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
        store.enqueueBatch(
            JSONObject()
                .put("id", UUID.randomUUID().toString())
                .put("playlist_id", manifest.getString("id"))
                .put("loop_started_at", startedAt.toString())
                .put("loop_ended_at", endedAt.toString())
                .put("captured_offline", capturedOffline)
                .put("events", events),
        )
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
            try {
                api.heartbeat(body)
            } catch (_: Exception) {
                // Health is best effort; playback and proof batches remain local.
            }
        }
    }

    private fun scheduleOperations() {
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
        operationsHandler.post(heartbeat)
        operationsHandler.post(sync)
    }

    private fun hasExternalPower(): Boolean {
        val battery = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val plugged = battery?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0
        return plugged != 0
    }

    private fun elapsedMs(): Long =
        currentStartedAt?.let {
            java.time.Duration.between(it, serverClock.now()).toMillis()
        } ?: 0

    private fun stopPlayback() {
        playbackHandler.removeCallbacksAndMessages(null)
        binding.video.stopPlayback()
        binding.video.visibility = View.GONE
        binding.image.visibility = View.GONE
    }

    private fun showStatus(message: String) {
        stopPlayback()
        binding.status.text = message
        binding.status.visibility = View.VISIBLE
    }

    private fun hideSystemUi() {
        window.decorView.systemUiVisibility =
            View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or
                View.SYSTEM_UI_FLAG_FULLSCREEN or
                View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
                View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or
                View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION or
                View.SYSTEM_UI_FLAG_LAYOUT_STABLE
    }

    private fun enterLockTaskIfManaged() {
        val manager = getSystemService(DevicePolicyManager::class.java)
        val admin = ComponentName(this, KioskDeviceAdminReceiver::class.java)
        if (manager.isDeviceOwnerApp(packageName)) {
            manager.setLockTaskPackages(admin, arrayOf(packageName))
            startLockTask()
        }
    }

    private fun recoverInterruptedPlayback() {
        val raw = store.state("current_playback")
        if (raw.isNullOrBlank()) return
        try {
            val manifest = activeManifest ?: return
            val previous = JSONObject(raw)
            val endedAt = serverClock.now()
            val startedAt = Instant.parse(previous.getString("started_at"))
            loopResults.clear()
            loopResults.addAll(persistedLoopResults())
            val interruptedItem = previous.getString("playlist_item_id")
            if (loopResults.none { it.playlistItemId == interruptedItem }) {
                loopResults += PlaybackResult(
                    id = previous.getString("result_id"),
                    playlistItemId = interruptedItem,
                    startedAt = startedAt.toString(),
                    endedAt = endedAt.toString(),
                    durationMs = java.time.Duration.between(startedAt, endedAt)
                        .toMillis()
                        .coerceAtLeast(0),
                    status = "interrupted",
                    failureReason = "app_restart_or_power_loss",
                )
            }
            val restoredLoopStartedAt = store.state("loop_started_at")
                ?.takeIf { it.isNotBlank() }
                ?.let { Instant.parse(it) }
                ?: startedAt
            enqueueLoopBatch(
                manifest = manifest,
                sourceResults = loopResults,
                startedAt = restoredLoopStartedAt,
                endedAt = endedAt,
                capturedOffline = true,
            )
            loopResults.clear()
            currentIndex = previous.optInt("item_index", 0)
        } catch (_: Exception) {
            currentIndex = 0
        } finally {
            store.putState("current_playback", "")
            store.putState("loop_results", "")
            loopStartedAt = null
        }
    }

    private fun isOnline(): Boolean {
        val connectivity = getSystemService(ConnectivityManager::class.java)
        val network = connectivity.activeNetwork ?: return false
        val capabilities = connectivity.getNetworkCapabilities(network) ?: return false
        return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }
}
