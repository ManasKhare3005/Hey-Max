package com.manaskhare.max

import android.content.Context
import android.net.Uri

/** Where Max lives (https://<laptop>.<tailnet>.ts.net) and the pairing token. */
class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("max", Context.MODE_PRIVATE)

    var url: String
        get() = sp.getString("url", "") ?: ""
        set(v) = sp.edit().putString("url", v.trim().trimEnd('/')).apply()

    var token: String
        get() = sp.getString("token", "") ?: ""
        set(v) = sp.edit().putString("token", v.trim()).apply()

    var speakReplies: Boolean
        get() = sp.getBoolean("speak", true)
        set(v) = sp.edit().putBoolean("speak", v).apply()

    /** Time (ms) of the last deadline alert shown, so alerts missed while offline are shown once.
     *  (Event ids restart when Max restarts; timestamps don't.) */
    var lastAlertAt: Long
        get() = sp.getLong("lastAlertAt", 0L)
        set(v) = sp.edit().putLong("lastAlertAt", v).apply()

    val paired: Boolean get() = url.isNotBlank() && token.isNotBlank()

    fun clear() = sp.edit().remove("url").remove("token").apply()

    /** max://pair?url=...&token=...  ->  saved; returns false if the link is not a pairing link. */
    fun pairFrom(link: String): Boolean {
        val uri = runCatching { Uri.parse(link.trim()) }.getOrNull() ?: return false
        if (uri.scheme != "max" || uri.host != "pair") return false
        val u = uri.getQueryParameter("url").orEmpty()
        val t = uri.getQueryParameter("token").orEmpty()
        if (!u.startsWith("http") || t.length < 20) return false
        url = u
        token = t
        return true
    }
}
