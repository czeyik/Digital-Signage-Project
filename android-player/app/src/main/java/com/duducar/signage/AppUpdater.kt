package com.duducar.signage

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/** Downloads and installs only a higher, same-signed production APK. */
class AppUpdater(context: Context) {
    private val applicationContext = context.applicationContext
    private val packageManager = applicationContext.packageManager
    private val updateDirectory = File(applicationContext.filesDir, "app-updates")
    private val executor = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())
    private val inFlight = AtomicBoolean(false)

    init {
        updateDirectory.mkdirs()
        // A process replacement may happen before PackageInstaller can deliver its
        // result. The next process can safely retry because PackageInstaller has
        // already copied the session contents by commit time.
        pendingPreferences().edit().clear().commit()
        updateDirectory.listFiles().orEmpty().forEach { it.delete() }
    }

    fun stage(
        metadata: AppUpdateMetadata,
        onReadyToInstall: (File) -> Boolean,
    ) {
        if (pendingPreferences().contains(PENDING_PATH)) return
        if (!inFlight.compareAndSet(false, true)) return
        executor.execute {
            val apk = downloadAndVerify(metadata)
            if (apk == null) {
                inFlight.set(false)
                return@execute
            }
            mainHandler.post {
                if (!onReadyToInstall(apk)) discard(apk)
            }
        }
    }

    fun install(apk: File, metadata: AppUpdateMetadata): Boolean {
        var session: PackageInstaller.Session? = null
        return try {
            if (!apk.isFile || !matchesPackageAndSignature(apk, metadata)) {
                discard(apk)
                return false
            }
            val params = PackageInstaller.SessionParams(
                PackageInstaller.SessionParams.MODE_FULL_INSTALL,
            ).apply {
                setAppPackageName(applicationContext.packageName)
                setSize(apk.length())
                setInstallReason(PackageManager.INSTALL_REASON_POLICY)
                setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            }
            val installer = packageManager.packageInstaller
            val sessionId = installer.createSession(params)
            session = installer.openSession(sessionId)
            session.openWrite("base.apk", 0, apk.length()).use { output ->
                apk.inputStream().use { input -> input.copyTo(output, COPY_BUFFER_BYTES) }
                session.fsync(output)
            }
            pendingPreferences().edit()
                .putString(PENDING_PATH, apk.absolutePath)
                .commit()
            val resultIntent = Intent(applicationContext, AppUpdateReceiver::class.java).apply {
                action = ACTION_INSTALL_RESULT
            }
            val result = PendingIntent.getBroadcast(
                applicationContext,
                metadata.versionCode,
                resultIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE,
            )
            session.commit(result.intentSender)
            session.close()
            session = null
            inFlight.set(false)
            true
        } catch (_: Exception) {
            session?.abandon()
            pendingPreferences().edit().clear().commit()
            discard(apk)
            inFlight.set(false)
            false
        }
    }

    fun discard(apk: File) {
        apk.delete()
        inFlight.set(false)
    }

    fun close() {
        executor.shutdownNow()
    }

    private fun downloadAndVerify(metadata: AppUpdateMetadata): File? {
        val uri = runCatching { URI(metadata.downloadUrl) }.getOrNull() ?: return null
        if (uri.scheme != "https" || uri.host.isNullOrBlank()) return null
        val target = File(
            updateDirectory,
            "update-${metadata.versionCode}-${metadata.sha256}.apk",
        )
        if (target.isFile && target.length() == metadata.sizeBytes &&
            sha256(target) == metadata.sha256 && matchesPackageAndSignature(target, metadata)
        ) {
            return target
        }
        target.delete()
        val temporary = File(updateDirectory, ".${target.name}.${System.nanoTime()}.download")
        var connection: HttpURLConnection? = null
        return try {
            connection = URL(metadata.downloadUrl).openConnection() as HttpURLConnection
            connection.instanceFollowRedirects = false
            connection.connectTimeout = 20_000
            connection.readTimeout = 120_000
            connection.useCaches = false
            connection.setRequestProperty("Accept", "application/vnd.android.package-archive")
            if (connection.responseCode != HttpURLConnection.HTTP_OK) return null
            if (connection.contentLengthLong > metadata.sizeBytes) return null
            connection.inputStream.use { input ->
                FileOutputStream(temporary).use { output ->
                    val buffer = ByteArray(COPY_BUFFER_BYTES)
                    var total = 0L
                    while (true) {
                        val count = input.read(buffer)
                        if (count <= 0) break
                        total += count
                        if (total > metadata.sizeBytes) return null
                        output.write(buffer, 0, count)
                    }
                    output.fd.sync()
                }
            }
            if (
                temporary.length() != metadata.sizeBytes ||
                sha256(temporary) != metadata.sha256 ||
                !matchesPackageAndSignature(temporary, metadata)
            ) {
                return null
            }
            moveReplacing(temporary, target)
            target.takeIf { it.isFile }
        } catch (_: Exception) {
            null
        } finally {
            connection?.disconnect()
            temporary.delete()
        }
    }

    private fun matchesPackageAndSignature(file: File, metadata: AppUpdateMetadata): Boolean {
        val archive = packageManager.getPackageArchiveInfo(
            file.absolutePath,
            PackageManager.GET_SIGNING_CERTIFICATES,
        ) ?: return false
        if (archive.packageName != applicationContext.packageName) return false
        if (archive.longVersionCode != metadata.versionCode.toLong()) return false
        if (archive.versionName != metadata.versionName) return false
        val installed = packageManager.getPackageInfo(
            applicationContext.packageName,
            PackageManager.GET_SIGNING_CERTIFICATES,
        )
        val archiveSigners = signingDigests(archive)
        val installedSigners = signingDigests(installed)
        return archiveSigners.isNotEmpty() && archiveSigners == installedSigners
    }

    private fun signingDigests(info: PackageInfo): Set<String> =
        info.signingInfo?.apkContentsSigners
            ?.map { signer -> sha256(signer.toByteArray()) }
            ?.toSet()
            .orEmpty()

    private fun sha256(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes)
            .joinToString("") { "%02x".format(it) }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(COPY_BUFFER_BYTES)
            while (true) {
                val count = input.read(buffer)
                if (count <= 0) break
                digest.update(buffer, 0, count)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun moveReplacing(source: File, target: File) {
        try {
            Files.move(
                source.toPath(),
                target.toPath(),
                StandardCopyOption.ATOMIC_MOVE,
                StandardCopyOption.REPLACE_EXISTING,
            )
        } catch (_: AtomicMoveNotSupportedException) {
            Files.move(
                source.toPath(),
                target.toPath(),
                StandardCopyOption.REPLACE_EXISTING,
            )
        }
    }

    private fun pendingPreferences() =
        applicationContext.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)

    companion object {
        const val ACTION_INSTALL_RESULT = "com.duducar.signage.action.APP_UPDATE_RESULT"
        private const val PREFERENCES = "app_update"
        private const val PENDING_PATH = "pending_path"
        private const val COPY_BUFFER_BYTES = 1024 * 1024

        fun clearPending(context: Context) {
            val preferences = context.applicationContext.getSharedPreferences(
                PREFERENCES,
                Context.MODE_PRIVATE,
            )
            preferences.getString(PENDING_PATH, null)?.let { File(it).delete() }
            preferences.edit().clear().commit()
        }
    }
}

class AppUpdateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == AppUpdater.ACTION_INSTALL_RESULT) {
            AppUpdater.clearPending(context)
        }
    }
}
