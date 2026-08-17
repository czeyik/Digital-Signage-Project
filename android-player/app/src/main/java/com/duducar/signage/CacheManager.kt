package com.duducar.signage

import android.content.Context
import android.graphics.BitmapFactory
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import java.util.UUID

class CacheManager(private val context: Context) {
    private val mediaDir = File(context.filesDir, "media").apply { mkdirs() }
    private val manifestFile = File(context.filesDir, "active-manifest.json")
    private val stagedManifestFile = File(context.filesDir, "staged-manifest.json")

    fun activeManifest(): JSONObject? = readValidatedManifest(manifestFile)

    fun discardStaged() {
        stagedManifestFile.delete()
    }

    /**
     * Downloads may leave harmless immutable media behind, but a manifest is
     * staged only after it is valid, complete, and still current to its caller.
     */
    fun prepare(
        manifest: JSONObject,
        commit: (ManifestIdentity, JSONObject) -> Boolean = { identity, candidate ->
            stageCandidate(identity, candidate)
        },
    ): ManifestIdentity? {
        val serialized = manifest.toString()
        if (serialized.toByteArray(Charsets.UTF_8).size > ManifestPolicy.MAX_MANIFEST_BYTES) {
            return null
        }
        val candidate = runCatching { JSONObject(serialized) }.getOrNull() ?: return null
        val identity = ManifestPolicy.validate(candidate, BuildConfig.VERSION_NAME) ?: return null
        discardStaged()
        mediaDir.listFiles().orEmpty()
            .filter { it.name.endsWith(".download") }
            .forEach { it.delete() }

        val items = candidate.getJSONArray("items")
        val mediaSizes = mutableMapOf<String, Long>()
        for (index in 0 until items.length()) {
            val item = items.getJSONObject(index)
            mediaSizes.putIfAbsent(item.getString("media_id"), item.getLong("size_bytes"))
        }
        val requiredBytes = mediaSizes.values.fold(0L) { total, size -> total + size }
        val cacheLimit = candidate.getLong("media_cache_bytes")
        val minimumFree = candidate.getLong("minimum_free_bytes")
        val cachedBytes = mediaDir.listFiles().orEmpty().sumOf { it.length() }
        val downloadBytes = (0 until items.length())
            .asSequence()
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
        ) return null

        for (index in 0 until items.length()) {
            if (!downloadAndValidate(items.getJSONObject(index))) return null
        }
        return identity.takeIf { commit(it, candidate) }
    }

    /** Caller serializes this with the manifest generation that selected it. */
    fun stageCandidate(identity: ManifestIdentity, candidate: JSONObject): Boolean =
        ManifestPolicy.validate(candidate, BuildConfig.VERSION_NAME) == identity &&
            writeJsonAtomically(stagedManifestFile, candidate)

    /**
     * The active file is replaced only after the candidate parses and matches
     * the identity selected by the latest successful synchronization.
     */
    fun activateStaged(expected: ManifestIdentity?): JSONObject? {
        val candidate = readValidatedManifest(stagedManifestFile) ?: return null
        if (expected == null || ManifestPolicy.identity(candidate) != expected) return null
        if (!writeJsonAtomically(manifestFile, candidate)) return null
        stagedManifestFile.delete()
        prune(candidate)
        return candidate
    }

    fun mediaFile(mediaId: String): File = File(mediaDir, mediaId)

    fun validatedMediaFile(item: JSONObject): File? {
        val file = mediaFile(item.getString("media_id"))
        return file.takeIf {
            it.isFile &&
                it.length() == item.getLong("size_bytes") &&
                sha256(it) == item.getString("sha256") &&
                (item.getString("kind") != "image" || hasSafeImageBounds(it))
        }
    }

    private fun readValidatedManifest(file: File): JSONObject? = runCatching {
        if (!file.isFile || file.length() > ManifestPolicy.MAX_MANIFEST_BYTES) return@runCatching null
        JSONObject(file.readText()).takeIf {
            ManifestPolicy.validate(it, BuildConfig.VERSION_NAME) != null
        }
    }.getOrNull()

    private fun downloadAndValidate(item: JSONObject): Boolean {
        val target = mediaFile(item.getString("media_id"))
        if (validatedMediaFile(item) != null) return true
        val temporary = File(mediaDir, "${target.name}.${UUID.randomUUID()}.download")
        return try {
            val expectedSize = item.getLong("size_bytes")
            val connection = URL(item.getString("download_url")).openConnection() as HttpURLConnection
            connection.instanceFollowRedirects = false
            connection.connectTimeout = 20_000
            connection.readTimeout = 60_000
            connection.useCaches = false
            if (connection.responseCode != HttpURLConnection.HTTP_OK) return false
            val declaredSize = connection.contentLengthLong
            if (declaredSize > expectedSize) return false
            connection.inputStream.use { input ->
                FileOutputStream(temporary).use { output ->
                    val buffer = ByteArray(1024 * 1024)
                    var total = 0L
                    while (true) {
                        val count = input.read(buffer)
                        if (count <= 0) break
                        total += count
                        if (total > expectedSize) throw IllegalStateException("Oversized download")
                        output.write(buffer, 0, count)
                    }
                    output.fd.sync()
                }
            }
            if (temporary.length() != expectedSize || sha256(temporary) != item.getString("sha256")) {
                false
            } else {
                if (!moveReplacing(temporary, target)) {
                    false
                } else if (validatedMediaFile(item) != null) {
                    true
                } else {
                    target.delete()
                    false
                }
            }
        } catch (_: Exception) {
            false
        } finally {
            temporary.delete()
        }
    }

    private fun writeJsonAtomically(target: File, value: JSONObject): Boolean {
        val temporary = File(target.parentFile, ".${target.name}.${UUID.randomUUID()}.tmp")
        return try {
            FileOutputStream(temporary).use { output ->
                output.write(value.toString().toByteArray(Charsets.UTF_8))
                output.fd.sync()
            }
            moveReplacing(temporary, target)
        } catch (_: Exception) {
            false
        } finally {
            temporary.delete()
        }
    }

    private fun hasSafeImageBounds(file: File): Boolean {
        val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.path, options)
        return ImageDecodePolicy.hasSafeBounds(options.outWidth, options.outHeight)
    }

    private fun moveReplacing(source: File, target: File): Boolean = try {
        Files.move(
            source.toPath(),
            target.toPath(),
            StandardCopyOption.ATOMIC_MOVE,
            StandardCopyOption.REPLACE_EXISTING,
        )
        true
    } catch (_: AtomicMoveNotSupportedException) {
        Files.move(source.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING)
        true
    } catch (_: Exception) {
        false
    }

    private fun prune(manifest: JSONObject) {
        val retained = buildSet {
            val items = manifest.getJSONArray("items")
            for (index in 0 until items.length()) add(items.getJSONObject(index).getString("media_id"))
        }
        mediaDir.listFiles()?.forEach { file ->
            if (file.name !in retained && !file.name.endsWith(".download")) file.delete()
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
