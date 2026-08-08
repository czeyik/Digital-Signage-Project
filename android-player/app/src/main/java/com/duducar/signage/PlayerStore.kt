package com.duducar.signage

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import org.json.JSONObject

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
        readableDatabase.query(
            "state",
            arrayOf("value"),
            "key = ?",
            arrayOf(key),
            null,
            null,
            null,
        ).use { cursor -> if (cursor.moveToFirst()) cursor.getString(0) else null }

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
}
