package com.duducar.signage

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.nio.file.StandardCopyOption

class CacheManager(private val context: Context) {
    private val mediaDir = File(context.filesDir, "media").apply { mkdirs() }
    private val manifestFile = File(context.filesDir, "active-manifest.json")
    private val stagedManifestFile = File(context.filesDir, "staged-manifest.json")

    fun activeManifest(): JSONObject? =
        try {
            if (manifestFile.exists()) JSONObject(manifestFile.readText()) else null
        } catch (_: Exception) {
            null
        }

    fun prepare(manifest: JSONObject): Boolean {
        mediaDir.listFiles().orEmpty()
            .filter { it.name.endsWith(".download") }
            .forEach { it.delete() }
        val items = manifest.getJSONArray("items")
        val mediaSizes = mutableMapOf<String, Long>()
        for (index in 0 until items.length()) {
            val item = items.getJSONObject(index)
            mediaSizes[item.getString("media_id")] = item.getLong("size_bytes")
        }
        val requiredBytes = mediaSizes.values.sum()
        val cacheLimit = manifest.optLong(
            "media_cache_bytes",
            10L * 1024 * 1024 * 1024,
        )
        val minimumFree = manifest.optLong(
            "minimum_free_bytes",
            2L * 1024 * 1024 * 1024,
        )
        val cachedBytes = mediaDir.listFiles().orEmpty().sumOf { it.length() }
        val downloadBytes = (0 until items.length())
            .map { items.getJSONObject(it) }
            .distinctBy { it.getString("media_id") }
            .filter { validatedMediaFile(it) == null }
            .sumOf { it.getLong("size_bytes") }
        if (!StoragePolicy.canStage(
                requiredBytes,
                cachedBytes,
                downloadBytes,
                context.filesDir.usableSpace,
                cacheLimit,
                minimumFree,
            )
        ) return false
        for (index in 0 until items.length()) {
            val item = items.getJSONObject(index)
            if (!downloadAndValidate(item)) return false
        }
        stagedManifestFile.writeText(manifest.toString())
        return true
    }

    fun activateStaged(): JSONObject? {
        if (!stagedManifestFile.exists()) return null
        return try {
            java.nio.file.Files.move(
                stagedManifestFile.toPath(),
                manifestFile.toPath(),
                StandardCopyOption.ATOMIC_MOVE,
                StandardCopyOption.REPLACE_EXISTING,
            )
            JSONObject(manifestFile.readText()).also { prune(it) }
        } catch (_: Exception) {
            null
        }
    }

    fun mediaFile(mediaId: String): File = File(mediaDir, mediaId)

    fun validatedMediaFile(item: JSONObject): File? {
        val file = mediaFile(item.getString("media_id"))
        return file.takeIf {
            it.exists() &&
                it.length() == item.getLong("size_bytes") &&
                sha256(it) == item.getString("sha256")
        }
    }

    private fun downloadAndValidate(item: JSONObject): Boolean {
        val target = mediaFile(item.getString("media_id"))
        if (target.exists() && sha256(target) == item.getString("sha256")) return true
        val temporary = File(mediaDir, "${target.name}.download")
        return try {
            val connection = URL(item.getString("download_url")).openConnection() as HttpURLConnection
            connection.connectTimeout = 20_000
            connection.readTimeout = 60_000
            val expectedSize = item.getLong("size_bytes")
            connection.inputStream.use { input ->
                temporary.outputStream().use { output ->
                    val buffer = ByteArray(1024 * 1024)
                    var total = 0L
                    while (true) {
                        val count = input.read(buffer)
                        if (count <= 0) break
                        total += count
                        if (total > expectedSize) {
                            throw IllegalStateException("Oversized download")
                        }
                        output.write(buffer, 0, count)
                    }
                }
            }
            if (temporary.length() != expectedSize || sha256(temporary) != item.getString("sha256")) {
                temporary.delete()
                false
            } else {
                if (target.exists()) target.delete()
                temporary.renameTo(target)
            }
        } catch (_: Exception) {
            temporary.delete()
            false
        }
    }

    private fun prune(manifest: JSONObject) {
        val retained = buildSet {
            val items = manifest.getJSONArray("items")
            for (index in 0 until items.length()) {
                add(items.getJSONObject(index).getString("media_id"))
            }
        }
        mediaDir.listFiles()?.forEach { file ->
            if (!retained.contains(file.name)) file.delete()
        }
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(1024 * 1024)
            while (true) {
                val count = input.read(buffer)
                if (count <= 0) break
                digest.update(buffer, 0, count)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}
