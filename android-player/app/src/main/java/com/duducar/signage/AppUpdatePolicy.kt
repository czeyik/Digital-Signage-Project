package com.duducar.signage

import org.json.JSONObject
import java.net.URI

data class AppUpdateMetadata(
    val versionCode: Int,
    val versionName: String,
    val downloadUrl: String,
    val sha256: String,
    val sizeBytes: Long,
)

object AppUpdatePolicy {
    const val MAX_UPDATE_BYTES = 200L * 1024 * 1024
    const val MIN_FREE_BYTES_AFTER_UPDATE = 256L * 1024 * 1024

    private val versionNamePattern = Regex("[0-9]+\\.[0-9]+\\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?")
    private val sha256Pattern = Regex("[0-9a-f]{64}")

    fun parse(payload: JSONObject?, currentVersionCode: Int): AppUpdateMetadata? {
        if (payload == null) return null
        return runCatching {
            parse(
                versionCode = payload.getInt("version_code"),
                versionName = payload.getString("version_name"),
                downloadUrl = payload.getString("download_url"),
                sha256 = payload.getString("sha256"),
                sizeBytes = payload.getLong("size_bytes"),
                currentVersionCode = currentVersionCode,
            )
        }.getOrNull()?.takeIf { it.versionCode > currentVersionCode }
    }

    fun parse(
        versionCode: Int,
        versionName: String,
        downloadUrl: String,
        sha256: String,
        sizeBytes: Long,
        currentVersionCode: Int,
    ): AppUpdateMetadata? = runCatching {
        val normalizedName = versionName.trim()
        val normalizedUrl = downloadUrl.trim()
        val normalizedSha256 = sha256.trim().lowercase()
        val uri = URI(normalizedUrl)
        require(versionCode > currentVersionCode)
        require(versionNamePattern.matches(normalizedName))
        require(uri.scheme == "https" && !uri.host.isNullOrBlank())
        require(uri.userInfo == null && uri.fragment == null)
        require(sha256Pattern.matches(normalizedSha256))
        require(sizeBytes in 1..MAX_UPDATE_BYTES)
        AppUpdateMetadata(versionCode, normalizedName, normalizedUrl, normalizedSha256, sizeBytes)
    }.getOrNull()

    fun mayStage(
        isProduction: Boolean,
        isDeviceOwner: Boolean,
        shutdownPrepared: Boolean,
        adminSessionActive: Boolean,
        usableBytes: Long,
        updateSizeBytes: Long,
        batteryPercent: Int?,
        charging: Boolean?,
    ): Boolean {
        if (!isProduction || !isDeviceOwner || shutdownPrepared || adminSessionActive) {
            return false
        }
        if (usableBytes < updateSizeBytes + MIN_FREE_BYTES_AFTER_UPDATE) return false
        if (charging == true) return true
        return batteryPercent != null && batteryPercent >= 50
    }
}
