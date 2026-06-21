package com.duducar.signage

import android.content.Context
import java.time.Instant

class ServerClock(context: Context) {
    private val preferences = context.getSharedPreferences("server_clock", Context.MODE_PRIVATE)

    fun update(serverTime: String) {
        val serverMillis = Instant.parse(serverTime).toEpochMilli()
        val offset = serverMillis - System.currentTimeMillis()
        preferences.edit().putLong("offset_ms", offset).apply()
    }

    fun now(): Instant {
        val offset = preferences.getLong("offset_ms", 0)
        return Instant.ofEpochMilli(System.currentTimeMillis() + offset)
    }
}

