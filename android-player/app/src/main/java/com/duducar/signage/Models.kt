package com.duducar.signage

data class MediaEntry(
    val entryId: String,
    val mediaId: String,
    val kind: String,
    val sha256: String,
    val sizeBytes: Long,
    val durationMs: Long,
    val downloadUrl: String,
)

data class PlaylistManifest(
    val id: String,
    val version: Int,
    val urgent: Boolean,
    val items: List<MediaEntry>,
)

data class PlaybackResult(
    val id: String,
    val playlistItemId: String,
    val startedAt: String,
    val endedAt: String?,
    val durationMs: Long,
    val status: String,
    val failureReason: String = "",
)

