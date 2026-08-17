package com.duducar.signage

data class PlaybackResult(
    val id: String,
    val playlistItemId: String,
    val startedAt: String,
    val endedAt: String?,
    val durationMs: Long,
    val status: String,
    val failureReason: String = "",
)
