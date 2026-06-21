package com.duducar.signage

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

class CacheManager(context: Context) {
    private val mediaDir = File(context.filesDir, "media").apply { mkdirs() }
    private val manifestFile = File(context.filesDir, "active-manifest.json")
    private val stagedManifestFile = File(context.filesDir, "staged-manifest.json")

    fun activeManifest(): JSONObject? =
        if (manifestFile.exists()) JSONObject(manifestFile.readText()) else null

    fun prepare(manifest: JSONObject): Boolean {
        val items = manifest.getJSONArray("items")
        for (index in 0 until items.length()) {
            val item = items.getJSONObject(index)
            if (!downloadAndValidate(item)) return false
        }
        stagedManifestFile.writeText(manifest.toString())
        return true
    }

    fun activateStaged(): JSONObject? {
        if (!stagedManifestFile.exists()) return null
        if (manifestFile.exists()) manifestFile.delete()
        if (!stagedManifestFile.renameTo(manifestFile)) return null
        prune(JSONObject(manifestFile.readText()))
        return JSONObject(manifestFile.readText())
    }

    fun mediaFile(mediaId: String): File = File(mediaDir, mediaId)

    private fun downloadAndValidate(item: JSONObject): Boolean {
        val target = mediaFile(item.getString("media_id"))
        if (target.exists() && sha256(target) == item.getString("sha256")) return true
        val temporary = File(mediaDir, "${target.name}.download")
        return try {
            val connection = URL(item.getString("download_url")).openConnection() as HttpURLConnection
            connection.connectTimeout = 20_000
            connection.readTimeout = 60_000
            connection.inputStream.use { input ->
                temporary.outputStream().use { output -> input.copyTo(output) }
            }
            val expectedSize = item.getLong("size_bytes")
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
            if (!retained.contains(file.name) && !file.name.endsWith(".download")) file.delete()
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

