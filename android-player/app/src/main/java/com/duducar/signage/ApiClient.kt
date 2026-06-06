package com.duducar.signage

import android.os.Build
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL

class ApiClient(private val credentials: CredentialStore) {
    private var accessToken: String? = null

    fun enroll(code: String, androidId: String): JSONObject {
        val body = JSONObject()
            .put("code", code)
            .put("android_id", androidId)
            .put("android_version", Build.VERSION.RELEASE)
            .put("app_version", BuildConfig.VERSION_NAME)
            .put("integrity_compromised", IntegrityChecks.isCompromised())
        val response = request("devices/enroll/", "POST", body, authenticated = false)
        credentials.saveRefreshToken(response.getString("refresh_token"))
        accessToken = response.getString("access_token")
        return response
    }

    fun manifest(): JSONObject = authenticatedRequest("devices/sync/", "GET")

    fun heartbeat(body: JSONObject): JSONObject =
        authenticatedRequest("devices/heartbeat/", "POST", body)

    fun uploadBatch(body: JSONObject): JSONObject =
        authenticatedRequest("devices/playback-batches/", "POST", body)

    private fun authenticatedRequest(path: String, method: String, body: JSONObject? = null): JSONObject {
        if (accessToken == null) refreshAccessToken()
        return try {
            request(path, method, body, authenticated = true)
        } catch (error: UnauthorizedException) {
            refreshAccessToken()
            request(path, method, body, authenticated = true)
        }
    }

    private fun refreshAccessToken() {
        val refresh = credentials.refreshToken() ?: throw UnauthorizedException()
        val response = request(
            "devices/token/",
            "POST",
            JSONObject().put("refresh_token", refresh),
            authenticated = false,
        )
        accessToken = response.getString("access_token")
    }

    private fun request(
        path: String,
        method: String,
        body: JSONObject? = null,
        authenticated: Boolean,
    ): JSONObject {
        val connection = URL(BuildConfig.API_BASE_URL + path).openConnection() as HttpURLConnection
        connection.requestMethod = method
        connection.connectTimeout = 15_000
        connection.readTimeout = 30_000
        connection.setRequestProperty("Accept", "application/json")
        connection.setRequestProperty("Content-Type", "application/json")
        if (authenticated) connection.setRequestProperty("Authorization", "Bearer $accessToken")
        if (body != null) {
            connection.doOutput = true
            connection.outputStream.use { it.write(body.toString().toByteArray()) }
        }
        val status = connection.responseCode
        if (status == 401 || status == 403) throw UnauthorizedException()
        val stream = if (status in 200..299) connection.inputStream else connection.errorStream
        val text = stream.bufferedReader().use(BufferedReader::readText)
        if (status !in 200..299) throw ApiException(status, text)
        return JSONObject(text)
    }
}

class UnauthorizedException : RuntimeException()
class ApiException(val status: Int, message: String) : RuntimeException(message)

object IntegrityChecks {
    fun isCompromised(): Boolean {
        val suspicious = listOf(
            "/system/bin/su",
            "/system/xbin/su",
            "/sbin/su",
            "/data/local/bin/su",
        )
        return suspicious.any { java.io.File(it).exists() } ||
            android.os.Build.TAGS?.contains("test-keys") == true
    }
}

