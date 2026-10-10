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
        com.manaskhare.max.ui.Looks.load(this)
        registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
            override fun onActivityStarted(a: android.app.Activity) { Hub.visible++ }
            override fun onActivityStopped(a: android.app.Activity) { Hub.visible = (Hub.visible - 1).coerceAtLeast(0) }
            override fun onActivityCreated(a: android.app.Activity, b: android.os.Bundle?) {}
            override fun onActivityResumed(a: android.app.Activity) {}
            override fun onActivityPaused(a: android.app.Activity) {}
            override fun onActivitySaveInstanceState(a: android.app.Activity, b: android.os.Bundle) {}
            override fun onActivityDestroyed(a: android.app.Activity) {}
        })
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
                NotificationChannel(CH_RECORD, "Recording", NotificationManager.IMPORTANCE_LOW).apply {
                    description = "While a lecture or meeting is being recorded on the phone"
                    setShowBadge(false)
                },
            )
        )
        Sync.lastSync.value = Prefs(this).lastSync
        startLink(this)
        // Working without the laptop: a recording cut off by a crash is kept, reminders are armed
        RecordService.recover(this)
        runCatching { Alarms.armAll(this) }
        Sync.trigger(this, 1500)
    }

    companion object {
        const val CH_LINK = "link"
        const val CH_APPROVAL = "approvals"
        const val CH_REMINDER = "reminders"
        const val CH_UPDATE = "updates"
        const val CH_RECORD = "recording"

        /** Start (or poke) the foreground service that keeps the WebSocket open. */
        fun startLink(context: Context) {
            if (!Prefs(context).paired) return
            ContextCompat.startForegroundService(context, Intent(context, MaxService::class.java))
        }
    }
}
