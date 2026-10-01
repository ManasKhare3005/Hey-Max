package com.manaskhare.max

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification

/**
 * Keeps the last few notifications in memory (never on disk) so Max can answer "what did I miss?"
 * or "what did my last WhatsApp say?". Only works once the user turns on notification access
 * for Max (Settings → Phone powers). Ongoing ones (music, navigation, Max's own) are skipped.
 */
class MaxNotificationListener : NotificationListenerService() {
    data class Item(val app: String, val pkg: String, val title: String, val text: String, val time: Long)

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        val n = sbn.notification ?: return
        if (sbn.packageName == packageName || sbn.isOngoing || n.flags and Notification.FLAG_GROUP_SUMMARY != 0) return
        val extras = n.extras ?: return
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val text = (extras.getCharSequence(Notification.EXTRA_BIG_TEXT) ?: extras.getCharSequence(Notification.EXTRA_TEXT))
            ?.toString().orEmpty().take(300)
        if (title.isBlank() && text.isBlank()) return
        val app = runCatching {
            packageManager.getApplicationLabel(packageManager.getApplicationInfo(sbn.packageName, 0)).toString()
        }.getOrDefault(sbn.packageName)
        synchronized(items) {
            items.removeAll { it.pkg == sbn.packageName && it.title == title && it.text == text }
            items.addFirst(Item(app, sbn.packageName, title, text, sbn.postTime))
            while (items.size > 60) items.removeLast()
        }
    }

    companion object {
        private val items = ArrayDeque<Item>()

        fun recent(app: String, count: Int): List<Item> = synchronized(items) {
            val q = app.trim().lowercase()
            items.filter { q.isBlank() || it.app.lowercase().contains(q) || it.pkg.contains(q) }.take(count)
        }
    }
}
