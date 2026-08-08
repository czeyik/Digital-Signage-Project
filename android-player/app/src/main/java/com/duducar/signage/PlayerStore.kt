package com.duducar.signage

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant

class PlayerStore(private val context: Context) :
    SQLiteOpenHelper(context, "player.db", null, 2) {

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """.trimIndent(),
        )
        db.execSQL(
            """
            CREATE TABLE pending_batches (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                acknowledged INTEGER NOT NULL DEFAULT 0
            )
            """.trimIndent(),
        )
        createOperationalTable(db)
    }

    private fun createOperationalTable(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS pending_operational_events (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """.trimIndent(),
        )
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        if (oldVersion < 2) createOperationalTable(db)
    }

    fun putState(key: String, value: String) {
        putState(writableDatabase, key, value)
    }

    private fun putState(database: SQLiteDatabase, key: String, value: String) {
        database.insertWithOnConflict(
            "state",
            null,
            ContentValues().apply {
                put("key", key)
                put("value", value)
            },
            SQLiteDatabase.CONFLICT_REPLACE,
        )
    }

    fun state(key: String): String? =
        state(readableDatabase, key)

    private fun state(database: SQLiteDatabase, key: String): String? =
        database.query(
            "state",
            arrayOf("value"),
            "key = ?",
            arrayOf(key),
            null,
            null,
            null,
        ).use { cursor -> if (cursor.moveToFirst()) cursor.getString(0) else null }

    fun hasPlannedShutdownMarker(): Boolean =
        !state(PLANNED_SHUTDOWN_MARKER).isNullOrBlank()

    fun plannedShutdownMarker(): PlannedShutdownMarker? =
        plannedShutdownMarker(readableDatabase)

    private fun plannedShutdownMarker(database: SQLiteDatabase): PlannedShutdownMarker? {
        return shutdownMarker(database, PLANNED_SHUTDOWN_MARKER)
    }

    private fun shutdownMarker(
        database: SQLiteDatabase,
        stateKey: String,
    ): PlannedShutdownMarker? {
        val rawMarker = state(database, stateKey) ?: return null
        if (rawMarker.isBlank()) return null
        return try {
            val value = JSONObject(rawMarker)
            val id = value.getString("id")
            java.util.UUID.fromString(id)
            val preparedAt = value.getLong("prepared_at_epoch_ms")
            if (preparedAt < 0) return null
            val orderlyAt = if (value.has("orderly_shutdown_at_epoch_ms") &&
                !value.isNull("orderly_shutdown_at_epoch_ms")
            ) {
                value.getLong("orderly_shutdown_at_epoch_ms").takeIf { it >= preparedAt }
            } else {
                null
            }
            val resumedAt = if (value.has("resumed_at_epoch_ms") &&
                !value.isNull("resumed_at_epoch_ms")
            ) {
                value.getLong("resumed_at_epoch_ms").takeIf { resumed ->
                    orderlyAt != null && resumed >= orderlyAt
                }
            } else {
                null
            }
            PlannedShutdownMarker(
                id = id,
                preparedAtEpochMs = preparedAt,
                orderlyShutdownAtEpochMs = orderlyAt,
                resumedAtEpochMs = resumedAt,
                requiresTrustedTimestampRebase = value.optBoolean(
                    "requires_trusted_timestamp_rebase",
                    false,
                ),
            )
        } catch (_: Exception) {
            null
        }
    }

    fun preparePlannedShutdown(
        marker: PlannedShutdownMarker,
        event: JSONObject,
    ): Boolean {
        val database = writableDatabase
        var created = false
        database.beginTransaction()
        try {
            if (state(database, PLANNED_SHUTDOWN_MARKER).isNullOrBlank()) {
                enqueueOperationalEvent(database, event)
                if (marker.requiresTrustedTimestampRebase) {
                    putUnanchoredPlannedShutdownEventIds(
                        database,
                        unanchoredPlannedShutdownEventIds(database) + marker.id,
                    )
                }
                putState(database, PLANNED_SHUTDOWN_MARKER, marker.toJson())
                created = true
            }
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
        return created
    }

    fun markPlannedShutdownOrderly(observedAtEpochMs: Long): Boolean {
        val database = writableDatabase
        var observed = false
        database.beginTransaction()
        try {
            val marker = plannedShutdownMarker(database)
            if (marker != null) {
                if (marker.orderlyShutdownAtEpochMs == null) {
                    putState(
                        database,
                        PLANNED_SHUTDOWN_MARKER,
                        marker.copy(
                            orderlyShutdownAtEpochMs = observedAtEpochMs.coerceAtLeast(
                                marker.preparedAtEpochMs,
                            ),
                        ).toJson(),
                    )
                }
                observed = true
            }
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
        return observed
    }

    fun clearPlannedShutdownMarker() {
        val database = writableDatabase
        database.beginTransaction()
        try {
            // The active marker gates all automatic playback. Once the person
            // deliberately resumes, retain only an orderly marker for exit
            // history classification; it must not keep the player stopped.
            plannedShutdownMarker(database)
                ?.takeIf { it.orderlyShutdownAtEpochMs != null }
                ?.let { marker ->
                    val orderlyShutdownAt = requireNotNull(marker.orderlyShutdownAtEpochMs)
                    val resumedAt = System.currentTimeMillis()
                    // If the local wall clock moved backwards, keeping a
                    // broad marker would be less safe than omitting
                    // suppression for this attempted orderly shutdown.
                    if (resumedAt >= orderlyShutdownAt) {
                        putState(
                            database,
                            RECENT_ORDERLY_PLANNED_SHUTDOWN_MARKER,
                            marker.copy(resumedAtEpochMs = resumedAt).toJson(),
                        )
                    }
                }
            putState(database, PLANNED_SHUTDOWN_MARKER, "")
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
    }

    fun recentOrderlyPlannedShutdownMarker(
        nowEpochMs: Long,
    ): PlannedShutdownMarker? {
        val database = writableDatabase
        val marker = shutdownMarker(database, RECENT_ORDERLY_PLANNED_SHUTDOWN_MARKER)
            ?.takeIf { it.orderlyShutdownAtEpochMs != null }
            ?: return null
        val orderlyShutdownAt = requireNotNull(marker.orderlyShutdownAtEpochMs)
        if (nowEpochMs > orderlyShutdownAt + ShutdownPreparationPolicy.ABNORMAL_EXIT_SUPPRESSION_WINDOW_MS) {
            putState(database, RECENT_ORDERLY_PLANNED_SHUTDOWN_MARKER, "")
            return null
        }
        return marker
    }

    fun clearRecentOrderlyPlannedShutdownMarker() {
        putState(RECENT_ORDERLY_PLANNED_SHUTDOWN_MARKER, "")
    }

    fun exitHistoryInstallationIdentity(): String {
        val database = writableDatabase
        var identity: String? = null
        database.beginTransaction()
        try {
            identity = state(database, EXIT_HISTORY_INSTALLATION_ID)
                ?.takeIf { value ->
                    runCatching { java.util.UUID.fromString(value) }.isSuccess
                }
            if (identity == null) {
                identity = java.util.UUID.randomUUID().toString()
                putState(database, EXIT_HISTORY_INSTALLATION_ID, requireNotNull(identity))
            }
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
        return requireNotNull(identity)
    }

    fun exitHistoryCursor(): ExitHistoryCursor {
        val rawCursor = state(EXIT_HISTORY_CURSOR) ?: return ExitHistoryCursor()
        if (rawCursor.isBlank()) return ExitHistoryCursor()
        return try {
            val value = JSONObject(rawCursor)
            val timestamp = value.getLong("timestamp_ms")
            if (timestamp < -1) return ExitHistoryCursor()
            val identities = buildSet {
                val values = value.optJSONArray("identities") ?: return@buildSet
                for (index in 0 until values.length()) {
                    values.optString(index).takeIf { it.isNotBlank() }?.let(::add)
                }
            }
            ExitHistoryCursor(timestamp, identities)
        } catch (_: Exception) {
            ExitHistoryCursor()
        }
    }

    fun enqueueOperationalEventsAndAdvanceExitHistoryCursor(
        events: Collection<JSONObject>,
        cursor: ExitHistoryCursor,
    ) {
        val database = writableDatabase
        database.beginTransaction()
        try {
            events.forEach { event -> enqueueOperationalEvent(database, event) }
            putState(
                database,
                EXIT_HISTORY_CURSOR,
                JSONObject()
                    .put("timestamp_ms", cursor.timestampMs)
                    .put("identities", cursor.identitiesAtTimestamp.sorted())
                    .toString(),
            )
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
    }

    /**
     * A planned shutdown can be requested before the first trusted server-time
     * anchor. The event remains durable and keeps its idempotency key, but its
     * timestamp is replaced before any upload so a user-edited clock cannot
     * block FIFO operational-event delivery with a future timestamp.
     */
    fun rebaseUnanchoredPlannedShutdownEvents(trustedRecordedAt: String): Int {
        val normalizedRecordedAt = Instant.parse(trustedRecordedAt).toString()
        val database = writableDatabase
        var rebased = 0
        database.beginTransaction()
        try {
            unanchoredPlannedShutdownEventIds(database).forEach { id ->
                val payload = database.query(
                    "pending_operational_events",
                    arrayOf("payload"),
                    "id = ?",
                    arrayOf(id),
                    null,
                    null,
                    null,
                ).use { cursor ->
                    if (cursor.moveToFirst()) cursor.getString(0) else null
                }
                val event = payload?.let { raw -> runCatching { JSONObject(raw) }.getOrNull() }
                if (
                    event != null &&
                        event.optString("id") == id &&
                        event.optString("kind") == "planned_shutdown"
                ) {
                    event.put("recorded_at", normalizedRecordedAt)
                    database.update(
                        "pending_operational_events",
                        ContentValues().apply { put("payload", event.toString()) },
                        "id = ?",
                        arrayOf(id),
                    )
                    rebased += 1
                }
            }
            putUnanchoredPlannedShutdownEventIds(database, emptySet())
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
        return rebased
    }

    fun enqueueBatch(
        batch: JSONObject,
        maxBytes: Long = 500L * 1024 * 1024,
        minimumFreeBytes: Long = 2L * 1024 * 1024 * 1024,
        recordedAt: String = java.time.Instant.now().toString(),
    ): JSONObject? {
        writableDatabase.insertWithOnConflict(
            "pending_batches",
            null,
            ContentValues().apply {
                put("id", batch.getString("id"))
                put("payload", batch.toString())
                put("created_at", System.currentTimeMillis())
            },
            SQLiteDatabase.CONFLICT_IGNORE,
        )
        return enforceStoragePolicy(maxBytes, minimumFreeBytes, recordedAt)
    }

    fun enqueueBatchAndClearPlaybackState(
        batch: JSONObject,
        maxBytes: Long,
        minimumFreeBytes: Long,
        recordedAt: String,
    ): JSONObject? {
        val database = writableDatabase
        database.beginTransaction()
        try {
            database.insertWithOnConflict(
                "pending_batches",
                null,
                ContentValues().apply {
                    put("id", batch.getString("id"))
                    put("payload", batch.toString())
                    put("created_at", System.currentTimeMillis())
                },
                SQLiteDatabase.CONFLICT_IGNORE,
            )
            putState(database, "current_playback", "")
            putState(database, "loop_results", "")
            putState(database, "loop_started_at", "")
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
        return enforceStoragePolicy(maxBytes, minimumFreeBytes, recordedAt)
    }

    fun clearPlaybackState() {
        val database = writableDatabase
        database.beginTransaction()
        try {
            putState(database, "current_playback", "")
            putState(database, "loop_results", "")
            putState(database, "loop_started_at", "")
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
    }

    fun recordCheckpointLossAndClear(event: JSONObject) {
        val database = writableDatabase
        database.beginTransaction()
        try {
            enqueueOperationalEvent(database, event)
            putState(database, "current_playback", "")
            putState(database, "loop_results", "")
            putState(database, "loop_started_at", "")
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
    }

    fun pendingBatches(): List<Pair<String, JSONObject>> {
        val values = mutableListOf<Pair<String, JSONObject>>()
        readableDatabase.query(
            "pending_batches",
            arrayOf("id", "payload"),
            "acknowledged = 0",
            null,
            null,
            null,
            "created_at",
        ).use { cursor ->
            while (cursor.moveToNext()) {
                values += cursor.getString(0) to JSONObject(cursor.getString(1))
            }
        }
        return values
    }

    fun acknowledgeBatch(id: String) {
        writableDatabase.delete("pending_batches", "id = ?", arrayOf(id))
    }

    fun enqueueOperationalEvent(event: JSONObject) {
        enqueueOperationalEvent(writableDatabase, event)
    }

    private fun enqueueOperationalEvent(database: SQLiteDatabase, event: JSONObject) {
        val id = event.optString("id").ifBlank { java.util.UUID.randomUUID().toString() }
        event.put("id", id)
        database.insertWithOnConflict(
            "pending_operational_events",
            null,
            ContentValues().apply {
                put("id", id)
                put("payload", event.toString())
                put("created_at", System.currentTimeMillis())
            },
            SQLiteDatabase.CONFLICT_IGNORE,
        )
    }

    fun pendingOperationalEvents(): List<Pair<String, JSONObject>> {
        val values = mutableListOf<Pair<String, JSONObject>>()
        readableDatabase.query(
            "pending_operational_events",
            arrayOf("id", "payload"),
            null,
            null,
            null,
            null,
            "created_at",
        ).use { cursor ->
            while (cursor.moveToNext()) {
                values += cursor.getString(0) to JSONObject(cursor.getString(1))
            }
        }
        return values
    }

    fun acknowledgeOperationalEvent(id: String) {
        writableDatabase.delete("pending_operational_events", "id = ?", arrayOf(id))
    }

    private fun enforceStoragePolicy(
        maxBytes: Long,
        minimumFreeBytes: Long,
        recordedAt: String,
    ): JSONObject? {
        writableDatabase.delete("pending_batches", "acknowledged = 1", null)
        val queueBytes = pendingBatchBytes()
        val removalTargetBytes = StoragePolicy.forcedQueueRemovalTargetBytes(
            queueBytes = queueBytes,
            usableBytes = context.filesDir.usableSpace,
            maxQueueBytes = maxBytes,
            minimumFreeBytes = minimumFreeBytes,
        )
        if (removalTargetBytes <= 0) return null
        var removed = 0
        var removedBytes = 0L
        val batchesToRemove = mutableListOf<Pair<String, Long>>()
        writableDatabase.query(
            "pending_batches",
            arrayOf("id", "length(payload)"),
            "acknowledged = 0",
            null,
            null,
            null,
            "created_at",
        ).use { cursor ->
            while (
                cursor.moveToNext() &&
                removedBytes < removalTargetBytes
            ) {
                val id = cursor.getString(0)
                val bytes = cursor.getLong(1)
                batchesToRemove += id to bytes
                removed += 1
                removedBytes += bytes
            }
        }
        return if (batchesToRemove.isNotEmpty()) {
            val details = JSONObject()
                .put("removed_batches", removed)
                .put("estimated_removed_bytes", removedBytes)
                .put("target_removed_bytes", removalTargetBytes)
            val database = writableDatabase
            database.beginTransaction()
            try {
                batchesToRemove.forEach { (id, _) ->
                    database.delete("pending_batches", "id = ?", arrayOf(id))
                }
                enqueueOperationalEvent(
                    database,
                    JSONObject()
                        .put("kind", "forced_queue_loss")
                        .put("recorded_at", recordedAt)
                        .put("details", details),
                )
                database.setTransactionSuccessful()
            } finally {
                database.endTransaction()
            }
            details
        } else {
            null
        }
    }

    private fun pendingBatchBytes(): Long =
        readableDatabase.rawQuery(
            "SELECT COALESCE(SUM(length(payload)), 0) FROM pending_batches " +
                "WHERE acknowledged = 0",
            null,
        ).use { cursor -> if (cursor.moveToFirst()) cursor.getLong(0) else 0L }

    private fun PlannedShutdownMarker.toJson(): String =
        JSONObject()
            .put("id", id)
            .put("prepared_at_epoch_ms", preparedAtEpochMs)
            .put(
                "orderly_shutdown_at_epoch_ms",
                orderlyShutdownAtEpochMs ?: JSONObject.NULL,
            )
            .put("resumed_at_epoch_ms", resumedAtEpochMs ?: JSONObject.NULL)
            .put("requires_trusted_timestamp_rebase", requiresTrustedTimestampRebase)
            .toString()

    private fun unanchoredPlannedShutdownEventIds(
        database: SQLiteDatabase,
    ): Set<String> {
        val rawIds = state(database, UNANCHORED_PLANNED_SHUTDOWN_EVENT_IDS)
            ?.takeIf { it.isNotBlank() }
            ?: return emptySet()
        return runCatching {
            val values = JSONArray(rawIds)
            buildSet {
                for (index in 0 until values.length()) {
                    values.optString(index)
                        .takeIf { value ->
                            value.isNotBlank() &&
                                runCatching { java.util.UUID.fromString(value) }.isSuccess
                        }
                        ?.let(::add)
                }
            }
        }.getOrDefault(emptySet())
    }

    private fun putUnanchoredPlannedShutdownEventIds(
        database: SQLiteDatabase,
        ids: Set<String>,
    ) {
        val serialized = JSONArray().apply {
            ids.sorted().forEach(::put)
        }.toString()
        putState(database, UNANCHORED_PLANNED_SHUTDOWN_EVENT_IDS, serialized)
    }

    private companion object {
        const val PLANNED_SHUTDOWN_MARKER = "planned_shutdown_marker"
        const val RECENT_ORDERLY_PLANNED_SHUTDOWN_MARKER =
            "recent_orderly_planned_shutdown_marker"
        const val UNANCHORED_PLANNED_SHUTDOWN_EVENT_IDS =
            "unanchored_planned_shutdown_event_ids"
        const val EXIT_HISTORY_CURSOR = "exit_history_cursor"
        const val EXIT_HISTORY_INSTALLATION_ID = "exit_history_installation_id"
    }
}
