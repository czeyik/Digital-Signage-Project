package com.duducar.signage

import android.content.Context
import android.os.SystemClock
import android.provider.Settings
import java.time.Instant

class ServerClock(context: Context) {
    private val preferences = context.getSharedPreferences("server_clock", Context.MODE_PRIVATE)
    private val contentResolver = context.contentResolver

    fun update(serverTime: String) {
        val serverMillis = Instant.parse(serverTime).toEpochMilli()
        val wallMillis = System.currentTimeMillis()
        preferences.edit()
            .putLong("server_anchor_epoch_ms", serverMillis)
            .putLong("wall_anchor_epoch_ms", wallMillis)
            .putLong("elapsed_anchor_ms", SystemClock.elapsedRealtime())
            .putInt("anchor_boot_count", currentBootCount())
            // Retain the legacy offset for a safe rollback to an older APK.
            .putLong("offset_ms", serverMillis - wallMillis)
            .commit()
    }

    fun now(): Instant {
        if (preferences.contains("server_anchor_epoch_ms")) {
            val anchor = ServerClockAnchor(
                serverEpochMs = preferences.getLong("server_anchor_epoch_ms", 0),
                wallEpochMs = preferences.getLong("wall_anchor_epoch_ms", 0),
                elapsedRealtimeMs = preferences.getLong("elapsed_anchor_ms", 0),
                bootCount = preferences.getInt("anchor_boot_count", -1),
            )
            return Instant.ofEpochMilli(
                CorrectedClockPolicy.nowEpochMs(
                    anchor = anchor,
                    currentWallEpochMs = System.currentTimeMillis(),
                    currentElapsedRealtimeMs = SystemClock.elapsedRealtime(),
                    currentBootCount = currentBootCount(),
                ),
            )
        }
        val offset = preferences.getLong("offset_ms", 0)
        return Instant.ofEpochMilli(System.currentTimeMillis() + offset)
    }

    fun currentBootCount(): Int = Settings.Global.getInt(
        contentResolver,
        Settings.Global.BOOT_COUNT,
        -1,
    )
}
