package com.manaskhare.max

import android.Manifest
import android.content.ContentUris
import android.content.ContentValues
import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.CalendarContract
import android.provider.CalendarContract.Calendars
import android.provider.CalendarContract.Events
import java.time.Instant
import java.time.ZoneId
import java.time.ZoneOffset
import java.util.TimeZone

/**
 * The "Max" calendar on the phone: a local calendar (not tied to a Google account, so it works
 * offline) that Samsung Calendar / Google Calendar show next to the others. Max's events
 * (LocalDb.events) are copied into it after every sync; edits and deletions made in the calendar
 * app are read back first and become changes to send to the laptop.
 *
 * Max is this calendar's "sync adapter", the role Android gives the app that owns a calendar:
 * that's what lets it keep its own id (SYNC_DATA1 = uid) on each event and clear the edit flags.
 * Needs the calendar permission (Settings -> Calendar); without it events live in the app only.
 */
object MaxCalendar {
    private const val ACCOUNT = "Max"
    private const val COLOR = 0xFF22D3EE.toInt()

    fun allowed(context: Context) =
        context.checkSelfPermission(Manifest.permission.WRITE_CALENDAR) == PackageManager.PERMISSION_GRANTED &&
        context.checkSelfPermission(Manifest.permission.READ_CALENDAR) == PackageManager.PERMISSION_GRANTED

    private fun asAdapter(uri: Uri): Uri = uri.buildUpon()
        .appendQueryParameter(CalendarContract.CALLER_IS_SYNCADAPTER, "true")
        .appendQueryParameter(Calendars.ACCOUNT_NAME, ACCOUNT)
        .appendQueryParameter(Calendars.ACCOUNT_TYPE, CalendarContract.ACCOUNT_TYPE_LOCAL).build()

    /** The Max calendar's id, made on first use. */
    fun calendarId(context: Context): Long? {
        val cr = context.contentResolver
        cr.query(Calendars.CONTENT_URI, arrayOf(Calendars._ID),
                 "${Calendars.ACCOUNT_NAME} = ? AND ${Calendars.ACCOUNT_TYPE} = ?",
                 arrayOf(ACCOUNT, CalendarContract.ACCOUNT_TYPE_LOCAL), null)?.use { if (it.moveToFirst()) return it.getLong(0) }
        val uri = cr.insert(asAdapter(Calendars.CONTENT_URI), ContentValues().apply {
            put(Calendars.ACCOUNT_NAME, ACCOUNT)
            put(Calendars.ACCOUNT_TYPE, CalendarContract.ACCOUNT_TYPE_LOCAL)
            put(Calendars.NAME, "max")
            put(Calendars.CALENDAR_DISPLAY_NAME, "Max")
            put(Calendars.CALENDAR_COLOR, COLOR)
            put(Calendars.CALENDAR_ACCESS_LEVEL, Calendars.CAL_ACCESS_OWNER)
            put(Calendars.OWNER_ACCOUNT, ACCOUNT)
            put(Calendars.VISIBLE, 1)
            put(Calendars.SYNC_EVENTS, 1)
            put(Calendars.CALENDAR_TIME_ZONE, TimeZone.getDefault().id)
        }) ?: return null
        return ContentUris.parseId(uri)
    }

    // All-day events are stored at UTC midnight (Android's rule); ours keep local midnight.
    private fun allDayToUtc(localMs: Long): Long {
        val day = Instant.ofEpochMilli(localMs).atZone(ZoneId.systemDefault()).toLocalDate()
        return day.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli()
    }

    private fun allDayFromUtc(utcMs: Long): Long {
        val day = Instant.ofEpochMilli(utcMs).atZone(ZoneOffset.UTC).toLocalDate()
        return day.atStartOfDay(ZoneId.systemDefault()).toInstant().toEpochMilli()
    }

    /** Edits made in the calendar app since the last copy: changed / deleted / new events. */
    fun readBack(context: Context) {
        if (!allowed(context)) return
        val cal = calendarId(context) ?: return
        val db = LocalDb.get(context)
        val cr = context.contentResolver
        val now = System.currentTimeMillis()
        cr.query(asAdapter(Events.CONTENT_URI),
                 arrayOf(Events._ID, Events.SYNC_DATA1, Events.TITLE, Events.DTSTART, Events.DTEND, Events.ALL_DAY,
                         Events.EVENT_LOCATION, Events.DELETED, Events.DIRTY),
                 "${Events.CALENDAR_ID} = ? AND (${Events.DIRTY} = 1 OR ${Events.DELETED} = 1)",
                 arrayOf(cal.toString()), null)?.use { c ->
            while (c.moveToNext()) {
                val id = c.getLong(0)
                val uid = c.getString(1).orEmpty()
                val allDay = c.getInt(5) == 1
                val start = c.getLong(3).let { if (allDay) allDayFromUtc(it) else it }
                val end = (if (c.isNull(4)) c.getLong(3) + 3_600_000 else c.getLong(4)).let { if (allDay) allDayFromUtc(it) else it }
                val local = if (uid.isNotBlank()) db.event(uid) else null
                when {
                    c.getInt(7) == 1 -> {                                   // deleted in the calendar app
                        if (local != null && local.status != "cancelled")
                            db.put(local.copy(status = "cancelled", updatedMs = now, dirty = true))
                        cr.delete(asAdapter(ContentUris.withAppendedId(Events.CONTENT_URI, id)), null, null)
                    }
                    local != null -> db.put(local.copy(title = c.getString(2).orEmpty().ifBlank { local.title },
                        startMs = start, endMs = end, allDay = allDay, location = c.getString(6).orEmpty(),
                        updatedMs = now, dirty = true))
                    else -> {                                               // made in the calendar app: adopt it
                        val e = LocalEvent(LocalDb.newUid(), c.getString(2).orEmpty().ifBlank { "Event" }, start, end,
                                           allDay, c.getString(6).orEmpty(), updatedMs = now)
                        db.put(e)
                        cr.update(asAdapter(ContentUris.withAppendedId(Events.CONTENT_URI, id)),
                                  ContentValues().apply { put(Events.SYNC_DATA1, e.uid) }, null, null)
                    }
                }
                if (c.getInt(7) != 1)
                    cr.update(asAdapter(ContentUris.withAppendedId(Events.CONTENT_URI, id)),
                              ContentValues().apply { put(Events.DIRTY, 0) }, null, null)
            }
        }
    }

    /** Make the Max calendar match LocalDb.events: add / update active ones, remove the rest. */
    fun mirror(context: Context) {
        if (!allowed(context)) return
        val cal = calendarId(context) ?: return
        val cr = context.contentResolver
        val ids = mutableMapOf<String, Long>()
        cr.query(asAdapter(Events.CONTENT_URI), arrayOf(Events._ID, Events.SYNC_DATA1),
                 "${Events.CALENDAR_ID} = ?", arrayOf(cal.toString()), null)?.use { c ->
            while (c.moveToNext()) ids[c.getString(1).orEmpty()] = c.getLong(0)
        }
        val keep = mutableSetOf<String>()
        for (e in LocalDb.get(context).events("status = 'active'")) {
            keep += e.uid
            val values = ContentValues().apply {
                put(Events.CALENDAR_ID, cal)
                put(Events.TITLE, e.title)
                put(Events.EVENT_LOCATION, e.location)
                put(Events.ALL_DAY, if (e.allDay) 1 else 0)
                put(Events.DTSTART, if (e.allDay) allDayToUtc(e.startMs) else e.startMs)
                put(Events.DTEND, if (e.allDay) allDayToUtc(e.endMs) else e.endMs)
                put(Events.EVENT_TIMEZONE, if (e.allDay) "UTC" else TimeZone.getDefault().id)
                put(Events.SYNC_DATA1, e.uid)
                put(Events.DIRTY, 0)
            }
            val existing = ids[e.uid]
            if (existing != null) {
                cr.update(asAdapter(ContentUris.withAppendedId(Events.CONTENT_URI, existing)), values, null, null)
            } else {
                val uri = cr.insert(asAdapter(Events.CONTENT_URI), values) ?: continue
                if (!e.allDay) cr.insert(asAdapter(CalendarContract.Reminders.CONTENT_URI), ContentValues().apply {
                    put(CalendarContract.Reminders.EVENT_ID, ContentUris.parseId(uri))
                    put(CalendarContract.Reminders.MINUTES, 15)                    // the calendar app alerts 15 min before
                    put(CalendarContract.Reminders.METHOD, CalendarContract.Reminders.METHOD_ALERT)
                })
            }
        }
        for ((uid, id) in ids) if (uid !in keep)
            cr.delete(asAdapter(ContentUris.withAppendedId(Events.CONTENT_URI, id)), null, null)
    }
}
