package com.duducar.signage

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import org.json.JSONObject

class PlayerStore(context: Context) :
    SQLiteOpenHelper(context, "player.db", null, 1) {

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
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit

    fun putState(key: String, value: String) {
        writableDatabase.insertWithOnConflict(
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

    fun enqueueBatch(batch: JSONObject) {
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
        pruneAcknowledged()
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
        writableDatabase.update(
            "pending_batches",
            ContentValues().apply { put("acknowledged", 1) },
            "id = ?",
            arrayOf(id),
        )
    }

    private fun pruneAcknowledged() {
        val dbFile = java.io.File(writableDatabase.path)
        if (dbFile.length() < 500L * 1024 * 1024) return
        writableDatabase.execSQL(
            """
            DELETE FROM pending_batches
            WHERE id IN (
                SELECT id FROM pending_batches
                WHERE acknowledged = 1
                ORDER BY created_at
                LIMIT 100
            )
            """.trimIndent(),
        )
    }
}

