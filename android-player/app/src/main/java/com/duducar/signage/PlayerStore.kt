package com.duducar.signage

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant

class PlayerStore(private val context: Context) :
    SQLiteOpenHelper(context, "player.db", null, 5) {

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
                payload_bytes INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            )
            """.trimIndent(),
        )
        createOperationalTable(db)
        createRejectedUploadTable(db)
        createLocationTable(db)
        createQueueIndexes(db)
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

    private fun createRejectedUploadTable(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS rejected_uploads (
                category TEXT NOT NULL,
                id TEXT NOT NULL,
                payload TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                rejected_at INTEGER NOT NULL,
                PRIMARY KEY (category, id)
            )
            """.trimIndent(),
        )
    }

    private fun createLocationTable(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS location_points (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                recorded_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            )
            """.trimIndent(),
        )
    }

    private fun createQueueIndexes(db: SQLiteDatabase) {
        db.execSQL(
            "CREATE INDEX IF NOT EXISTS pending_batches_created_idx ON pending_batches(created_at)",
        )
        db.execSQL(
            "CREATE INDEX IF NOT EXISTS pending_operational_events_created_idx " +
                "ON pending_operational_events(created_at)",
        )
        db.execSQL(
            "CREATE INDEX IF NOT EXISTS rejected_uploads_rejected_idx ON rejected_uploads(rejected_at)",
        )
        db.execSQL(
            "CREATE INDEX IF NOT EXISTS location_points_recorded_idx " +
                "ON location_points(recorded_at)",
        )
        db.execSQL(
            "CREATE INDEX IF NOT EXISTS location_points_created_idx " +
                "ON location_points(created_at)",
        )
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        if (oldVersion < 2) createOperationalTable(db)
        if (oldVersion < 3) {
            db.execSQL(
                "ALTER TABLE pending_batches ADD COLUMN payload_bytes INTEGER NOT NULL DEFAULT 0",
            )
            db.execSQL(
                "UPDATE pending_batches SET payload_bytes = length(CAST(payload AS BLOB))",
            )
        }
        if (oldVersion < 4) createRejectedUploadTable(db)
        if (oldVersion < 5) createLocationTable(db)
        createQueueIndexes(db)
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
        insertBatch(writableDatabase, batch)
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
            insertBatch(database, batch)
            putState(database, "current_playback", "")
            putState(database, "loop_results", "")
            putState(database, "loop_started_at", "")
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
        return enforceStoragePolicy(maxBytes, minimumFreeBytes, recordedAt)
    }

    private fun insertBatch(database: SQLiteDatabase, batch: JSONObject) {
        val payload = batch.toString()
        database.insertWithOnConflict(
            "pending_batches",
            null,
            ContentValues().apply {
                put("id", batch.getString("id"))
                put("payload", payload)
                put("payload_bytes", payload.toByteArray(Charsets.UTF_8).size)
                put("created_at", System.currentTimeMillis())
            },
            SQLiteDatabase.CONFLICT_IGNORE,
        )
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

    fun oldestPendingBatch(recordedAt: String): Pair<String, JSONObject>? =
        oldestPending(
            table = "pending_batches",
            discardInvalid = { id ->
                discardBatchAsPoison(id, LOCAL_CORRUPTION_STATUS, recordedAt)
            },
        )

    fun pendingBatch(id: String): JSONObject? =
        payloadFor(readableDatabase, "pending_batches", id)
            ?.let { payload -> runCatching { JSONObject(payload) }.getOrNull() }

    fun acknowledgeBatch(id: String) {
        writableDatabase.delete("pending_batches", "id = ?", arrayOf(id))
    }

    fun discardBatchAsPoison(
        id: String,
        statusCode: Int,
        recordedAt: String,
    ): Boolean {
        val database = writableDatabase
        database.beginTransaction()
        try {
            val payload = payloadFor(database, "pending_batches", id) ?: return false
            archiveRejectedUpload(database, "playback_batch", id, payload, statusCode)
            database.delete("pending_batches", "id = ?", arrayOf(id))
            enqueueOperationalEvent(
                database,
                JSONObject()
                    .put("kind", "forced_queue_loss")
                    .put("recorded_at", recordedAt)
                    .put(
                        "details",
                        JSONObject()
                            .put("removed_batches", 1)
                            .put(
                                "estimated_removed_bytes",
                                payload.toByteArray(Charsets.UTF_8).size,
                            )
                            .put("target_removed_bytes", 0),
                    ),
            )
            database.setTransactionSuccessful()
            return true
        } finally {
            database.endTransaction()
        }
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

    fun oldestPendingOperationalEvent(): Pair<String, JSONObject>? =
        oldestPending(
            table = "pending_operational_events",
            discardInvalid = { id ->
                discardAsPoison(
                    table = "pending_operational_events",
                    category = "operational_event",
                    id = id,
                    statusCode = LOCAL_CORRUPTION_STATUS,
                )
            },
        )

    fun pendingOperationalEvent(id: String): JSONObject? =
        payloadFor(readableDatabase, "pending_operational_events", id)
            ?.let { payload -> runCatching { JSONObject(payload) }.getOrNull() }

    fun acknowledgeOperationalEvent(id: String) {
        writableDatabase.delete("pending_operational_events", "id = ?", arrayOf(id))
    }

    fun discardOperationalEventAsPoison(id: String, statusCode: Int): Boolean =
        discardAsPoison(
            table = "pending_operational_events",
            category = "operational_event",
            id = id,
            statusCode = statusCode,
        )

    /**
     * Persist a location point before any network work. The queue is capped by
     * row count so a disconnected tablet cannot consume unbounded storage.
     */
    fun enqueueLocationPoint(
        point: JSONObject,
        recordedAtEpochMs: Long,
        recordedAt: String,
    ): LocationQueueLoss? {
        val payload = point.toString()
        val database = writableDatabase
        database.beginTransaction()
        try {
            database.insertWithOnConflict(
                "location_points",
                null,
                ContentValues().apply {
                    put("id", point.getString("id"))
                    put("payload", payload)
                    put("recorded_at", recordedAtEpochMs)
                    put("created_at", System.currentTimeMillis())
                },
                SQLiteDatabase.CONFLICT_IGNORE,
            )
            val count = locationPointCount(database)
            val removeCount = (count - LocationPolicy.QUEUE_CAPACITY).coerceAtLeast(0)
            if (removeCount == 0) {
                database.setTransactionSuccessful()
                return null
            }
            val removals = mutableListOf<Pair<String, Long>>()
            database.query(
                "location_points",
                arrayOf("id", "length(CAST(payload AS BLOB))"),
                null,
                null,
                null,
                null,
                "created_at ASC, rowid ASC",
                removeCount.toString(),
            ).use { cursor ->
                while (cursor.moveToNext()) {
                    removals += cursor.getString(0) to cursor.getLong(1)
                }
            }
            var removedBytes = 0L
            removals.forEach { (id, bytes) ->
                removedBytes += bytes
                database.delete("location_points", "id = ?", arrayOf(id))
            }
            val details = JSONObject()
                .put("removed_points", removals.size)
                .put("estimated_removed_bytes", removedBytes)
                .put("retained_points", locationPointCount(database))
            enqueueOperationalEvent(
                database,
                JSONObject()
                    .put("kind", "location_queue_loss")
                    .put("recorded_at", recordedAt)
                    .put("details", details),
            )
            database.setTransactionSuccessful()
            return LocationQueueLoss(removals.size, removedBytes)
        } finally {
            database.endTransaction()
        }
    }

    fun pendingLocationBatch(
        limit: Int = 500,
        currentPointId: String? = state(LOCATION_CURRENT_POINT_ID),
    ): List<Pair<String, JSONObject>> {
        val safeLimit = limit.coerceIn(1, 500)
        val rows = mutableListOf<Pair<String, JSONObject>>()
        val cursor = if (currentPointId.isNullOrBlank()) {
            readableDatabase.rawQuery(
                "SELECT id, payload FROM location_points " +
                    "ORDER BY created_at ASC, rowid ASC LIMIT ?",
                arrayOf(safeLimit.toString()),
            )
        } else {
            readableDatabase.rawQuery(
                "SELECT id, payload FROM location_points " +
                    "ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END, " +
                    "created_at ASC, rowid ASC LIMIT ?",
                arrayOf(currentPointId, safeLimit.toString()),
            )
        }
        cursor.use {
            while (cursor.moveToNext()) {
                val id = cursor.getString(0)
                runCatching { JSONObject(cursor.getString(1)) }
                    .onSuccess { rows += id to it }
            }
        }
        return rows
    }

    fun hasPendingLocationPoints(): Boolean =
        locationPointCount(readableDatabase) > 0

    fun locationPointCount(): Int = locationPointCount(readableDatabase)

    private fun locationPointCount(database: SQLiteDatabase): Int =
        database.rawQuery("SELECT COUNT(*) FROM location_points", null).use { cursor ->
            if (cursor.moveToFirst()) cursor.getInt(0) else 0
        }

    fun acknowledgeLocationPoints(ids: Collection<String>) {
        if (ids.isEmpty()) return
        val database = writableDatabase
        database.beginTransaction()
        try {
            ids.forEach { id -> database.delete("location_points", "id = ?", arrayOf(id)) }
            val current = state(database, LOCATION_CURRENT_POINT_ID)
            if (current != null && ids.contains(current)) {
                putState(database, LOCATION_CURRENT_POINT_ID, "")
            }
            database.setTransactionSuccessful()
        } finally {
            database.endTransaction()
        }
    }

    fun discardLocationPointAsPoison(
        id: String,
        reason: String,
        statusCode: Int,
        recordedAt: String,
    ): Boolean {
        val database = writableDatabase
        database.beginTransaction()
        try {
            val payload = payloadFor(database, "location_points", id) ?: return false
            archiveRejectedUpload(database, "location_point", id, payload, statusCode)
            database.delete("location_points", "id = ?", arrayOf(id))
            enqueueOperationalEvent(
                database,
                JSONObject()
                    .put("kind", "location_point_rejected")
                    .put("recorded_at", recordedAt)
                    .put(
                        "details",
                        JSONObject().put("point_id", id).put("reason", reason),
                    ),
            )
            database.setTransactionSuccessful()
            return true
        } finally {
            database.endTransaction()
        }
    }

    fun setLocationCurrentPointId(id: String?) {
        putState(LOCATION_CURRENT_POINT_ID, id.orEmpty())
    }

    fun setLocationCollectionState(state: JSONObject) {
        putState(LOCATION_COLLECTION_STATE, state.toString())
    }

    fun pendingLocationCollectionState(): JSONObject? =
        state(LOCATION_COLLECTION_STATE)
            ?.takeIf { it.isNotBlank() }
            ?.let { runCatching { JSONObject(it) }.getOrNull() }

    fun acknowledgeLocationCollectionState(sent: JSONObject) {
        val database = writableDatabase
        val current = state(database, LOCATION_COLLECTION_STATE)
        if (current == sent.toString()) putState(database, LOCATION_COLLECTION_STATE, "")
    }

    private fun oldestPending(
        table: String,
        discardInvalid: (String) -> Boolean,
    ): Pair<String, JSONObject>? {
        while (true) {
            val row = readableDatabase.query(
                table,
                arrayOf("id", "payload"),
                null,
                null,
                null,
                null,
                "created_at",
                "1",
            ).use { cursor ->
                if (cursor.moveToFirst()) cursor.getString(0) to cursor.getString(1) else null
            } ?: return null
            val parsed = runCatching { row.first to JSONObject(row.second) }.getOrNull()
            if (parsed != null) return parsed
            // A locally corrupted row must not block later FIFO evidence.
            if (!discardInvalid(row.first)) return null
        }
    }

    private fun discardAsPoison(
        table: String,
        category: String,
        id: String,
        statusCode: Int,
    ): Boolean {
        val database = writableDatabase
        database.beginTransaction()
        try {
            val payload = payloadFor(database, table, id) ?: return false
            archiveRejectedUpload(database, category, id, payload, statusCode)
            database.delete(table, "id = ?", arrayOf(id))
            database.setTransactionSuccessful()
            return true
        } finally {
            database.endTransaction()
        }
    }

    private fun payloadFor(database: SQLiteDatabase, table: String, id: String): String? =
        database.query(
            table,
            arrayOf("payload"),
            "id = ?",
            arrayOf(id),
            null,
            null,
            null,
        ).use { cursor -> if (cursor.moveToFirst()) cursor.getString(0) else null }

    private fun archiveRejectedUpload(
        database: SQLiteDatabase,
        category: String,
        id: String,
        payload: String,
        statusCode: Int,
    ) {
        database.insertWithOnConflict(
            "rejected_uploads",
            null,
            ContentValues().apply {
                put("category", category)
                put("id", id)
                put("payload", payload)
                put("status_code", statusCode)
                put("rejected_at", System.currentTimeMillis())
            },
            SQLiteDatabase.CONFLICT_REPLACE,
        )
        // ponytail: retain a small local diagnostic sample only; a support
        // export path can replace this cap if long-lived rejected evidence is needed.
        database.execSQL(
            "DELETE FROM rejected_uploads WHERE rowid NOT IN " +
                "(SELECT rowid FROM rejected_uploads ORDER BY rejected_at DESC LIMIT $MAX_REJECTED_UPLOADS)",
        )
    }

    private fun enforceStoragePolicy(
        maxBytes: Long,
        minimumFreeBytes: Long,
        recordedAt: String,
    ): JSONObject? {
        val queueBytes = pendingBatchBytes()
        val removalTargetBytes = StoragePolicy.forcedQueueRemovalTargetBytes(
            queueBytes = queueBytes,
            usableBytes = context.filesDir.usableSpace,
            maxQueueBytes = maxBytes.coerceAtLeast(1),
            minimumFreeBytes = minimumFreeBytes,
        )
        if (removalTargetBytes <= 0) return null
        var removed = 0
        var removedBytes = 0L
        val batchesToRemove = mutableListOf<Pair<String, Long>>()
        writableDatabase.query(
            "pending_batches",
            arrayOf("id", "payload_bytes"),
            null,
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
            "SELECT COALESCE(SUM(payload_bytes), 0) FROM pending_batches",
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
        const val LOCATION_CURRENT_POINT_ID = "location_current_point_id"
        const val LOCATION_COLLECTION_STATE = "location_collection_state"
        const val MAX_REJECTED_UPLOADS = 100
        const val LOCAL_CORRUPTION_STATUS = 0
    }
}
