package com.manaskhare.max

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat

class MaxApp : Application() {
    override fun onCreate() {
        super.onCreate()
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannels(
            listOf(
                NotificationChannel(CH_LINK, "Connection", NotificationManager.IMPORTANCE_MIN).apply {
                    description = "The always-on link to Max on your laptop"
                    setShowBadge(false)
                },
                NotificationChannel(CH_APPROVAL, "Approvals", NotificationManager.IMPORTANCE_HIGH).apply {
                    description = "Max asks before risky actions (send, delete, power...)"
                },
                NotificationChannel(CH_REMINDER, "Reminders", NotificationManager.IMPORTANCE_HIGH).apply {
                    description = "Reminders you set with Max"
                },
                NotificationChannel(CH_UPDATE, "Updates", NotificationManager.IMPORTANCE_DEFAULT).apply {
                    description = "Notes ready, daily digest"
                },
            )
        )
        startLink(this)
    }

    companion object {
        const val CH_LINK = "link"
        const val CH_APPROVAL = "approvals"
        const val CH_REMINDER = "reminders"
        const val CH_UPDATE = "updates"

        /** Start (or poke) the foreground service that keeps the WebSocket open. */
        fun startLink(context: Context) {
            if (!Prefs(context).paired) return
            ContextCompat.startForegroundService(context, Intent(context, MaxService::class.java))
        }
    }
}
