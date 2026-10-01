package com.manaskhare.max

import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.Network
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.NotificationCompat
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject

/**
 * Always-connected link: a foreground service holding one WebSocket to Max's event stream.
 * Turns events into notifications (approvals with Approve/Deny, reminders, notes ready) and
 * reconnects with backoff when the laptop sleeps or the network changes.
 */
class MaxService : Service() {
    private val main = Handler(Looper.getMainLooper())
    private var socket: WebSocket? = null
    private var backoffMs = 2_000L
    private var generation = 0                 // ignores callbacks from sockets we've replaced
    private lateinit var nm: NotificationManager
    private val reconnect = Runnable { connect() }

    private val netCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            main.post { if (Hub.link.value != Link.ONLINE) { backoffMs = 2_000L; connect() } }
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        nm = getSystemService(NotificationManager::class.java)
        getSystemService(ConnectivityManager::class.java).registerDefaultNetworkCallback(netCallback)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val n = linkNotification("Connecting to Max…")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(LINK_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(LINK_ID, n)
        }
        if (!Prefs(this).paired) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (intent?.action == ACTION_RECONNECT || Hub.link.value != Link.ONLINE) {
            backoffMs = 2_000L
            connect()
        }
        return START_STICKY
    }

    override fun onDestroy() {
        main.removeCallbacks(reconnect)
        generation++
        socket?.close(1000, "bye")
        socket = null
        Hub.link.value = Link.OFF
        runCatching { getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(netCallback) }
        super.onDestroy()
    }

    private fun connect() {
        main.removeCallbacks(reconnect)
        val api = MaxApi.from(Prefs(this)) ?: return stopSelf()
        val gen = ++generation
        socket?.cancel()
        Hub.link.value = Link.CONNECTING
        socket = MaxApi.http.newWebSocket(Request.Builder().url(api.wsUrl).build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) = main.post {
                if (gen != generation) return@post
                backoffMs = 2_000L
                Hub.link.value = Link.ONLINE
                nm.notify(LINK_ID, linkNotification("Connected to Max"))
            }.let { }

            override fun onMessage(webSocket: WebSocket, text: String) {
                if (gen != generation) return
                runCatching { JSONObject(text) }.getOrNull()?.let { main.post { handle(it) } }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = main.post {
                if (gen == generation) retry(unauthorized = code == 4401)
            }.let { }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = main.post {
                if (gen == generation) retry(unauthorized = response?.code == 401 || response?.code == 403)
            }.let { }
        })
    }

    private fun retry(unauthorized: Boolean) {
        Hub.link.value = if (unauthorized) Link.UNAUTHORIZED else Link.CONNECTING
        nm.notify(LINK_ID, linkNotification(if (unauthorized) "Pairing rejected: pair again in the app"
                                            else "Waiting for the laptop…"))
        val delay = if (unauthorized) 60_000L else backoffMs
        backoffMs = (backoffMs * 2).coerceAtMost(60_000L)
        main.postDelayed(reconnect, delay)
    }

    private fun handle(event: JSONObject) {
        val kind = event.optString("kind")
        val data = event.optJSONObject("data") ?: JSONObject()
        when (kind) {
            "hello" -> {
                data.optJSONObject("state")?.optJSONObject("stage")?.let { Hub.stage.value = it.optString("stage") }
                // Rebuild the approvals still open (requests without a result yet)
                val recent = data.optJSONArray("recent")
                val open = linkedMapOf<Int, PendingApproval>()
                for (i in 0 until (recent?.length() ?: 0)) {
                    val e = recent!!.getJSONObject(i)
                    val d = e.optJSONObject("data") ?: continue
                    when (e.optString("kind")) {
                        "approval_request" -> open[d.optInt("id")] =
                            PendingApproval(d.optInt("id"), d.optString("prompt"), d.optString("tool"))
                        "approval_result" -> open.remove(d.optInt("id"))
                    }
                }
                Hub.approvals.value = open.values.toList()
                open.values.forEach { notifyApproval(it) }
            }
            "stage" -> Hub.stage.value = data.optString("stage")
            "approval_request" -> {
                val a = PendingApproval(data.optInt("id"), data.optString("prompt"), data.optString("tool"))
                Hub.addApproval(a)
                notifyApproval(a)
            }
            "approval_result" -> {
                val id = data.optInt("id")
                Hub.removeApproval(id)
                nm.cancel(APPROVAL_BASE + id)
            }
            "reminder" -> if (data.optString("action") == "fired") {
                notify(MaxApp.CH_REMINDER, "⏰ Reminder", data.optString("text"))
            }
            "notes" -> when (data.optString("action")) {
                "saved" -> notify(MaxApp.CH_UPDATE, "Notes ready", data.optString("title"))
                "failed" -> notify(MaxApp.CH_UPDATE, "Notes failed", data.optString("error"))
            }
            "digest" -> if (data.optString("error").isNotBlank()) {
                notify(MaxApp.CH_UPDATE, "Daily digest not sent", data.optString("error"))
            }
        }
        Hub.events.tryEmit(MaxEvent(kind, data))
    }

    private fun openApp(): PendingIntent = PendingIntent.getActivity(
        this, 0, Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    private fun linkNotification(text: String): Notification =
        NotificationCompat.Builder(this, MaxApp.CH_LINK)
            .setSmallIcon(R.drawable.ic_stat_max)
            .setContentTitle("Max")
            .setContentText(text)
            .setOngoing(true)
            .setSilent(true)
            .setContentIntent(openApp())
            .build()

    private var nextId = 5000
    private fun notify(channel: String, title: String, text: String) {
        nm.notify(nextId++, NotificationCompat.Builder(this, channel)
            .setSmallIcon(R.drawable.ic_stat_max)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setAutoCancel(true)
            .setContentIntent(openApp())
            .build())
    }

    private fun notifyApproval(a: PendingApproval) {
        fun action(approved: Boolean) = PendingIntent.getBroadcast(
            this, a.id * 2 + if (approved) 1 else 0,
            Intent(this, ApprovalReceiver::class.java)
                .putExtra(ApprovalReceiver.EXTRA_ID, a.id)
                .putExtra(ApprovalReceiver.EXTRA_APPROVED, approved),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        nm.notify(APPROVAL_BASE + a.id, NotificationCompat.Builder(this, MaxApp.CH_APPROVAL)
            .setSmallIcon(R.drawable.ic_stat_max)
            .setContentTitle("Max needs your OK")
            .setContentText(a.prompt)
            .setStyle(NotificationCompat.BigTextStyle().bigText(a.prompt))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setContentIntent(openApp())
            .addAction(0, "Approve", action(true))
            .addAction(0, "Deny", action(false))
            .build())
    }

    companion object {
        const val LINK_ID = 1
        const val APPROVAL_BASE = 1000
        const val ACTION_RECONNECT = "reconnect"
    }
}
