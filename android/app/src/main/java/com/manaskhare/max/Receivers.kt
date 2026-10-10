package com.manaskhare.max

import android.app.NotificationManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/** Approve / Deny buttons on an approval notification. */
class ApprovalReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val id = intent.getIntExtra(EXTRA_ID, -1)
        val approved = intent.getBooleanExtra(EXTRA_APPROVED, false)
        if (id < 0) return
        context.getSystemService(NotificationManager::class.java).cancel(MaxService.APPROVAL_BASE + id)
        val api = MaxApi.from(Prefs(context)) ?: return
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            runCatching { api.approve(id, approved) }     // 404 = already answered elsewhere
            Hub.removeApproval(id)
            pending.finish()
        }
    }

    companion object {
        const val EXTRA_ID = "id"
        const val EXTRA_APPROVED = "approved"
    }
}

/** Reconnect after a reboot or an app update, and re-arm reminders (a reboot clears alarms). */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        runCatching { Alarms.armAll(context) }
        MaxApp.startLink(context)
    }
}
