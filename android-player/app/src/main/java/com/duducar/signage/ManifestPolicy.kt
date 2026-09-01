package com.duducar.signage

import org.json.JSONArray
import org.json.JSONObject
import java.net.URI
import java.time.Instant
import java.time.LocalTime
import java.util.UUID

data class ManifestIdentity(val id: String, val version: Int)

/**
 * Treat a synchronized manifest as an untrusted network document until every
 * field that reaches storage, playback, or scheduling has been bounded.
 */
object ManifestPolicy {
    const val MAX_MANIFEST_BYTES = 1 * 1024 * 1024
    const val MAX_MEDIA_CACHE_BYTES = 10L * 1024 * 1024 * 1024
    const val MAX_EVENT_QUEUE_BYTES = 500L * 1024 * 1024
    private const val MAX_MINIMUM_FREE_BYTES = 4L * 1024 * 1024 * 1024
    private const val MAX_PLAYLIST_ITEMS = 100
    private const val IMAGE_DURATION_MS = 15_000L
    private const val MAX_VIDEO_DURATION_MS = 15_000L
    private const val MAX_PLAYLIST_DURATION_MS = 30L * 60 * 1000
    private const val REQUIRED_TIME_ZONE = "Asia/Kuala_Lumpur"
    private const val REQUIRED_DAILY_SYNC_TIME = "00:00:00"
    private const val MAX_HOSTNAME_LENGTH = 253
    private val sha256Pattern = Regex("^[a-f0-9]{64}$")
    private val ipv4LiteralPattern = Regex("^\\d{1,3}(?:\\.\\d{1,3}){3}$")

    fun identity(manifest: JSONObject): ManifestIdentity? = runCatching {
        ManifestIdentity(
            id = requiredUuid(manifest, "id"),
            version = requiredPositiveInt(manifest, "version"),
        )
    }.getOrNull()

    fun validate(manifest: JSONObject, requiredAppVersion: String): ManifestIdentity? = runCatching {
        require(manifest.toString().toByteArray(Charsets.UTF_8).size <= MAX_MANIFEST_BYTES)
        val identity = ManifestIdentity(
            id = requiredUuid(manifest, "id"),
            version = requiredPositiveInt(manifest, "version"),
        )
        requiredBoolean(manifest, "urgent")
        require(requiredString(manifest, "required_app_version", 64) == requiredAppVersion)
        require(requiredString(manifest, "sync_timezone", 64) == REQUIRED_TIME_ZONE)
        require(requiredString(manifest, "daily_sync_local_time", 8) == REQUIRED_DAILY_SYNC_TIME)
        val mediaOrigin = normalizedMediaOrigin(
            requiredString(manifest, "media_origin", MAX_HOSTNAME_LENGTH),
        )
        LocalTime.parse(REQUIRED_DAILY_SYNC_TIME)

        val startsAt = Instant.parse(requiredString(manifest, "starts_at", 64))
        val endsAt = Instant.parse(requiredString(manifest, "ends_at", 64))
        require(endsAt > startsAt)

        val cacheLimit = requiredBoundedLong(
            manifest,
            "media_cache_bytes",
            minimum = 1,
            maximum = MAX_MEDIA_CACHE_BYTES,
        )
        requiredBoundedLong(
            manifest,
            "event_queue_bytes",
            minimum = 1,
            maximum = MAX_EVENT_QUEUE_BYTES,
        )
        requiredBoundedLong(
            manifest,
            "minimum_free_bytes",
            minimum = 0,
            maximum = MAX_MINIMUM_FREE_BYTES,
        )
        val items = manifest.opt("items") as? JSONArray ?: invalid()
        require(items.length() in 1..MAX_PLAYLIST_ITEMS)
        val entryIds = mutableSetOf<String>()
        val mediaDefinitions = mutableMapOf<String, String>()
        var totalBytes = 0L
        var totalDurationMs = 0L
        for (index in 0 until items.length()) {
            val item = items.opt(index) as? JSONObject ?: invalid()
            val entryId = requiredUuid(item, "entry_id")
            require(entryIds.add(entryId))
            val mediaId = requiredUuid(item, "media_id")
            val kind = requiredString(item, "kind", 16)
            require(kind == "image" || kind == "video")
            val sha256 = requiredString(item, "sha256", 64)
            require(sha256Pattern.matches(sha256))
            val sizeBytes = requiredBoundedLong(
                item,
                "size_bytes",
                minimum = 1,
                maximum = cacheLimit,
            )
            val durationMs = requiredBoundedLong(
                item,
                "duration_ms",
                minimum = 1,
                maximum = MAX_VIDEO_DURATION_MS,
            )
            if (kind == "image") require(durationMs == IMAGE_DURATION_MS)
            validateDownloadUrl(
                value = requiredString(item, "download_url", 8 * 1024),
                mediaOrigin = mediaOrigin,
            )

            totalBytes = safeAdd(totalBytes, sizeBytes)
            totalDurationMs = safeAdd(totalDurationMs, durationMs)
            val definition = "$kind|$sha256|$sizeBytes|$durationMs"
            val previous = mediaDefinitions.put(mediaId, definition)
            require(previous == null || previous == definition)
        }
        require(totalBytes <= cacheLimit)
        require(totalDurationMs <= MAX_PLAYLIST_DURATION_MS)
        identity
    }.getOrNull()

    private fun requiredUuid(objectValue: JSONObject, field: String): String {
        val value = requiredString(objectValue, field, 36)
        require(UUID.fromString(value).toString() == value)
        return value
    }

    private fun requiredString(
        objectValue: JSONObject,
        field: String,
        maximumLength: Int = 256,
    ): String =
        (objectValue.opt(field) as? String)
            ?.takeIf { it.isNotBlank() && it == it.trim() && it.length <= maximumLength }
            ?: invalid()

    private fun requiredBoolean(objectValue: JSONObject, field: String): Boolean =
        objectValue.opt(field) as? Boolean ?: invalid()

    private fun requiredPositiveInt(objectValue: JSONObject, field: String): Int {
        val number = objectValue.opt(field) as? Number ?: invalid()
        val value = number.toLong()
        require(number.toDouble() == value.toDouble() && value in 1..Int.MAX_VALUE)
        return value.toInt()
    }

    private fun requiredBoundedLong(
        objectValue: JSONObject,
        field: String,
        minimum: Long,
        maximum: Long,
    ): Long {
        val number = objectValue.opt(field) as? Number ?: invalid()
        val value = number.toLong()
        require(number.toDouble() == value.toDouble() && value in minimum..maximum)
        return value
    }

    private fun normalizedMediaOrigin(value: String): String {
        val origin = value.lowercase()
        val uri = URI("https://$origin/")
        require(
            uri.host == origin &&
                uri.rawAuthority == origin &&
                uri.port == -1 &&
                uri.userInfo == null &&
                uri.query == null &&
                uri.fragment == null,
        )
        validateMediaHost(origin)
        return origin
    }

    private fun validateDownloadUrl(value: String, mediaOrigin: String) {
        val uri = URI(value)
        require(uri.scheme.equals("https", ignoreCase = true))
        val host = uri.host?.lowercase() ?: invalid()
        require(host == mediaOrigin)
        validateMediaHost(host)
        require(uri.userInfo == null && uri.fragment == null)
        require(uri.port == -1 || uri.port == 443)
        require(uri.path.startsWith("/"))
    }

    private fun validateMediaHost(host: String) {
        require(host != "localhost" && !host.endsWith(".localhost"))
        require(host.contains('.') && !host.endsWith('.'))
        require(!ipv4LiteralPattern.matches(host) && !host.contains(':'))
    }

    private fun safeAdd(left: Long, right: Long): Long {
        require(left <= Long.MAX_VALUE - right)
        return left + right
    }

    private fun invalid(): Nothing = throw IllegalArgumentException("Invalid manifest")
}
