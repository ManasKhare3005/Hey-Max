package com.manaskhare.max

import android.Manifest
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.SearchManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.BatteryManager
import android.provider.AlarmClock
import android.provider.CalendarContract
import android.provider.ContactsContract
import android.provider.MediaStore
import android.provider.Settings
import android.telephony.PhoneNumberUtils
import android.telephony.TelephonyManager
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject
import java.net.URLEncoder
import java.text.DateFormat
import java.util.Date
import java.util.Locale

/** What Max can do on this phone. Each action returns (ok, a short sentence Max speaks). */
object PhoneActions {
    data class Result(val ok: Boolean, val message: String)

    private fun ok(m: String) = Result(true, m)
    private fun no(m: String) = Result(false, m)
    private fun enc(s: String) = URLEncoder.encode(s, "UTF-8").replace("+", "%20")

    fun run(ctx: Context, action: String, p: JSONObject): Result = runCatching {
        when (action) {
            "open_app" -> openApp(ctx, p.optString("name"), p.optString("package"))
            "call" -> call(ctx, p.optString("contact"))
            "message" -> message(ctx, p.optString("contact"), p.optString("text"), p.optString("app", "sms"))
            "alarm" -> alarm(ctx, p.optInt("hour"), p.optInt("minute"), p.optString("label"))
            "timer" -> timer(ctx, p.optInt("seconds"), p.optString("label"))
            "navigate" -> navigate(ctx, p.optString("destination"), p.optString("mode", "driving"))
            "play" -> play(ctx, p.optString("query"), p.optString("app", "spotify"))
            "calendar" -> calendar(ctx, p.optString("title"), p.optLong("begin"), p.optLong("end"), p.optString("location"))
            "email" -> email(ctx, p.optString("to"), p.optString("subject"), p.optString("body"))
            "notifications" -> notifications(ctx, p.optString("app"), p.optInt("count", 8))
            "status" -> status(ctx)
            else -> no("The phone app doesn't know how to $action yet. Update the Max app.")
        }
    }.getOrElse { no("That didn't work on the phone: ${it.message ?: it.javaClass.simpleName}") }

    // ----- state sent to the laptop when the link connects -----
    fun state(ctx: Context): JSONObject {
        val apps = JSONArray()
        launchableApps(ctx).forEach { (label, pkg) -> apps.put(JSONObject().put("label", label).put("package", pkg)) }
        return JSONObject().put("apps", apps).put("battery", battery(ctx).first)
    }

    private fun launchableApps(ctx: Context): List<Pair<String, String>> {
        val pm = ctx.packageManager
        val main = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        return pm.queryIntentActivities(main, 0)
            .map { it.loadLabel(pm).toString() to it.activityInfo.packageName }
            .filter { it.second != ctx.packageName }
            .distinctBy { it.second }
            .sortedBy { it.first.lowercase() }
    }

    // ----- starting other apps: right away when allowed, otherwise as a tap-to-open notification -----
    private fun launch(ctx: Context, intent: Intent, done: String, what: String): Result {
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val canStart = Hub.visible > 0 || Settings.canDrawOverlays(ctx)
        if (canStart && runCatching { ctx.startActivity(intent) }.isSuccess) return ok(done)
        val nm = ctx.getSystemService(NotificationManager::class.java)
        val id = (System.currentTimeMillis() % 100000).toInt() + 7000
        nm.notify(id, NotificationCompat.Builder(ctx, MaxApp.CH_APPROVAL)
            .setSmallIcon(R.drawable.ic_stat_max)
            .setContentTitle("Max: tap to $what")
            .setContentText(done)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setContentIntent(PendingIntent.getActivity(ctx, id, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
            .build())
        return ok("I've sent it to your phone: tap the notification to $what.")
    }

    private fun installed(ctx: Context, pkg: String) =
        runCatching { ctx.packageManager.getPackageInfo(pkg, 0); true }.getOrDefault(false)

    private fun openApp(ctx: Context, name: String, pkg: String): Result {
        val apps = launchableApps(ctx)
        val target = apps.firstOrNull { it.second == pkg } ?: bestApp(apps, name) ?: return no("I couldn't find an app called $name on your phone.")
        val intent = ctx.packageManager.getLaunchIntentForPackage(target.second) ?: return no("${target.first} can't be opened.")
        return launch(ctx, intent, "Opening ${target.first}.", "open ${target.first}")
    }

    private fun bestApp(apps: List<Pair<String, String>>, name: String): Pair<String, String>? {
        val q = name.lowercase().trim()
        return apps.firstOrNull { it.first.lowercase() == q }
            ?: apps.filter { it.first.lowercase().startsWith(q) }.minByOrNull { it.first.length }
            ?: apps.filter { it.first.lowercase().contains(q) }.minByOrNull { it.first.length }
    }

    // ----- contacts -----
    private sealed class Who {
        data class Found(val name: String, val value: String) : Who()
        data class Many(val names: List<String>) : Who()
        data class Missing(val why: String) : Who()
    }

    private fun lookup(ctx: Context, query: String, email: Boolean = false): Who {
        val q = query.trim()
        if (!email && q.count { it.isDigit() } >= 5 && q.all { it.isDigit() || it in "+-() ." }) return Who.Found(q, q)
        if (email && "@" in q) return Who.Found(q, q)
        if (ContextCompat.checkSelfPermission(ctx, Manifest.permission.READ_CONTACTS) != PackageManager.PERMISSION_GRANTED) {
            return Who.Missing("I can't read your contacts yet: allow it in Max → Settings → Phone powers.")
        }
        val uri = if (email) ContactsContract.CommonDataKinds.Email.CONTENT_URI else ContactsContract.CommonDataKinds.Phone.CONTENT_URI
        val col = if (email) ContactsContract.CommonDataKinds.Email.ADDRESS else ContactsContract.CommonDataKinds.Phone.NUMBER
        val type = if (email) ContactsContract.CommonDataKinds.Email.TYPE else ContactsContract.CommonDataKinds.Phone.TYPE
        val rows = mutableListOf<Triple<String, String, Int>>()
        ctx.contentResolver.query(uri, arrayOf(ContactsContract.Contacts.DISPLAY_NAME, col, type), null, null, null)?.use { c ->
            while (c.moveToNext()) {
                val n = c.getString(0) ?: continue
                val v = c.getString(1) ?: continue
                rows += Triple(n, v, c.getInt(2))
            }
        }
        val ql = q.lowercase()
        val tiers = listOf<(String) -> Boolean>(
            { it.lowercase() == ql },
            { it.lowercase().startsWith(ql) },
            { n -> n.lowercase().split(" ").any { it.startsWith(ql) } },
            { it.lowercase().contains(ql) },
        )
        for (match in tiers) {
            val hits = rows.filter { match(it.first) }
            if (hits.isEmpty()) continue
            val names = hits.map { it.first }.distinct()
            if (names.size > 1) return Who.Many(names.take(4))
            val mobile = ContactsContract.CommonDataKinds.Phone.TYPE_MOBILE
            val best = hits.firstOrNull { !email && it.third == mobile } ?: hits.first()
            return Who.Found(best.first, best.second)
        }
        return Who.Missing("I couldn't find $query in your contacts.")
    }

    private fun which(m: Who.Many) = "Which one: ${m.names.joinToString(", ", limit = 3)}?"

    private fun call(ctx: Context, contact: String): Result = when (val w = lookup(ctx, contact)) {
        is Who.Many -> no(which(w))
        is Who.Missing -> no(w.why)
        is Who.Found -> {
            val direct = ContextCompat.checkSelfPermission(ctx, Manifest.permission.CALL_PHONE) == PackageManager.PERMISSION_GRANTED
            val intent = Intent(if (direct) Intent.ACTION_CALL else Intent.ACTION_DIAL, Uri.parse("tel:" + Uri.encode(w.value)))
            launch(ctx, intent, if (direct) "Calling ${w.name}." else "Dialling ${w.name}: tap call.", "call ${w.name}")
        }
    }

    private fun e164(ctx: Context, number: String): String {
        val iso = ctx.getSystemService(TelephonyManager::class.java)?.networkCountryIso?.uppercase()?.ifBlank { null } ?: "US"
        return (PhoneNumberUtils.formatNumberToE164(number, iso) ?: number).filter { it.isDigit() }
    }

    private fun message(ctx: Context, contact: String, text: String, app: String): Result = when (val w = lookup(ctx, contact)) {
        is Who.Many -> no(which(w))
        is Who.Missing -> no(w.why)
        is Who.Found -> if (app == "whatsapp") {
            if (!installed(ctx, "com.whatsapp")) no("WhatsApp isn't installed on your phone.")
            else launch(ctx, Intent(Intent.ACTION_VIEW, Uri.parse("https://wa.me/${e164(ctx, w.value)}?text=${enc(text)}")).setPackage("com.whatsapp"),
                        "Your WhatsApp to ${w.name} is ready: tap Send.", "send your WhatsApp to ${w.name}")
        } else {
            launch(ctx, Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:" + Uri.encode(w.value))).putExtra("sms_body", text),
                   "Your text to ${w.name} is ready: tap Send.", "send your text to ${w.name}")
        }
    }

    private fun clock(hour: Int, minute: Int): String =
        DateFormat.getTimeInstance(DateFormat.SHORT, Locale.US).format(Date(0).apply {
            val c = java.util.Calendar.getInstance(); c.set(java.util.Calendar.HOUR_OF_DAY, hour); c.set(java.util.Calendar.MINUTE, minute)
            time = c.timeInMillis
        })

    private fun alarm(ctx: Context, hour: Int, minute: Int, label: String) = launch(ctx,
        Intent(AlarmClock.ACTION_SET_ALARM).putExtra(AlarmClock.EXTRA_HOUR, hour).putExtra(AlarmClock.EXTRA_MINUTES, minute)
            .putExtra(AlarmClock.EXTRA_SKIP_UI, true).apply { if (label.isNotBlank()) putExtra(AlarmClock.EXTRA_MESSAGE, label) },
        "Alarm set for ${clock(hour, minute)}.", "set the alarm")

    private fun timer(ctx: Context, seconds: Int, label: String): Result {
        val spoken = when {
            seconds % 3600 == 0 -> "${seconds / 3600} hour" + if (seconds / 3600 > 1) "s" else ""
            seconds >= 3600 -> "${seconds / 3600} hour ${seconds % 3600 / 60} minutes"
            seconds % 60 == 0 -> "${seconds / 60} minute" + if (seconds / 60 > 1) "s" else ""
            else -> "$seconds seconds"
        }
        return launch(ctx, Intent(AlarmClock.ACTION_SET_TIMER).putExtra(AlarmClock.EXTRA_LENGTH, seconds)
            .putExtra(AlarmClock.EXTRA_SKIP_UI, true).apply { if (label.isNotBlank()) putExtra(AlarmClock.EXTRA_MESSAGE, label) },
            "Timer set for $spoken.", "start the timer")
    }

    private fun navigate(ctx: Context, destination: String, mode: String): Result {
        val m = if (mode in listOf("driving", "walking", "transit", "bicycling")) mode else "driving"
        val uri = Uri.parse("https://www.google.com/maps/dir/?api=1&destination=${enc(destination)}&travelmode=$m&dir_action=navigate")
        return launch(ctx, Intent(Intent.ACTION_VIEW, uri), "Starting directions to $destination.", "start directions")
    }

    private fun play(ctx: Context, query: String, app: String): Result {
        val (pkg, name) = when (app) {
            "youtube_music" -> "com.google.android.apps.youtube.music" to "YouTube Music"
            "youtube" -> "com.google.android.youtube" to "YouTube"
            else -> "com.spotify.music" to "Spotify"
        }
        if (app == "youtube") {
            return launch(ctx, Intent(Intent.ACTION_VIEW, Uri.parse("https://www.youtube.com/results?search_query=${enc(query)}")),
                          "Searching YouTube for $query.", "open YouTube")
        }
        if (!installed(ctx, pkg)) return no("$name isn't installed on your phone.")
        val search = Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH).setPackage(pkg)
            .putExtra(SearchManager.QUERY, query).putExtra(MediaStore.EXTRA_MEDIA_FOCUS, "vnd.android.cursor.item/*")
        val intent = if (search.resolveActivity(ctx.packageManager) != null) search
                     else Intent(Intent.ACTION_VIEW, Uri.parse("spotify:search:${enc(query)}")).setPackage(pkg)
        return launch(ctx, intent, "Playing $query on $name.", "play $query")
    }

    private fun calendar(ctx: Context, title: String, begin: Long, end: Long, location: String) = launch(ctx,
        Intent(Intent.ACTION_INSERT, CalendarContract.Events.CONTENT_URI)
            .putExtra(CalendarContract.Events.TITLE, title)
            .putExtra(CalendarContract.EXTRA_EVENT_BEGIN_TIME, begin)
            .putExtra(CalendarContract.EXTRA_EVENT_END_TIME, end)
            .apply { if (location.isNotBlank()) putExtra(CalendarContract.Events.EVENT_LOCATION, location) },
        "$title is ready in your calendar: tap Save.", "save the event")

    private fun email(ctx: Context, to: String, subject: String, body: String): Result = when (val w = lookup(ctx, to, email = true)) {
        is Who.Many -> no(which(w))
        is Who.Missing -> no(w.why)
        is Who.Found -> launch(ctx, Intent(Intent.ACTION_SENDTO, Uri.parse("mailto:${w.value}?subject=${enc(subject)}&body=${enc(body)}")),
                               "Your email to ${w.name} is ready: tap Send.", "send your email")
    }

    // ----- reading -----
    fun notificationAccess(ctx: Context): Boolean {
        val flat = Settings.Secure.getString(ctx.contentResolver, "enabled_notification_listeners") ?: return false
        val me = ComponentName(ctx, MaxNotificationListener::class.java).flattenToString()
        return flat.split(":").any { it == me }
    }

    private fun notifications(ctx: Context, app: String, count: Int): Result {
        if (!notificationAccess(ctx)) return no("Notification access is off: turn it on in Max → Settings → Phone powers.")
        val items = MaxNotificationListener.recent(app, count)
        if (items.isEmpty()) return ok(if (app.isBlank()) "No new notifications." else "No recent notifications from $app.")
        val now = System.currentTimeMillis()
        val lines = items.joinToString("\n") { n ->
            val ago = ((now - n.time) / 60000).let { if (it < 1) "just now" else if (it < 60) "${it}m ago" else "${it / 60}h ago" }
            "- ${n.app}: ${n.title}${if (n.text.isNotBlank()) ": ${n.text}" else ""} ($ago)"
        }
        return ok("Recent phone notifications (newest first):\n$lines")
    }

    private fun battery(ctx: Context): Pair<Int, Boolean> {
        val bm = ctx.getSystemService(BatteryManager::class.java)
        return bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY) to bm.isCharging
    }

    private fun status(ctx: Context): Result {
        val (pct, charging) = battery(ctx)
        val cm = ctx.getSystemService(ConnectivityManager::class.java)
        val caps = cm.getNetworkCapabilities(cm.activeNetwork)
        val net = when {
            caps == null -> "offline"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "on Wi-Fi"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "on mobile data"
            else -> "online"
        }
        return ok("Your phone is at $pct%${if (charging) " and charging" else ""}, $net.")
    }
}
