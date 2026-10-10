package com.manaskhare.max

import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.MediaRecorder
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.flow.MutableStateFlow
import java.io.File
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * Records a lecture or meeting on the phone, with or without the laptop: a foreground service
 * (the screen can be off) writing AAC to the app's files. AAC in an ADTS stream rather than an
 * .m4a: if the phone dies mid-lecture, everything up to that moment is still readable.
 * 16 kHz mono at 32 kbps (~14 MB an hour) is what Whisper wants anyway.
 * When it stops, the recording waits for the laptop; Sync uploads it and the laptop writes the notes.
 * The phone only hears its own microphone (Android doesn't let apps record other apps' calls).
 */
class RecordService : Service() {
    private var recorder: MediaRecorder? = null
    private var current: LocalRecording? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> if (current == null) begin(intent.getStringExtra("kind") ?: "lecture", intent.getStringExtra("title").orEmpty())
            else -> if (current == null) stopSelf()
        }
        return START_NOT_STICKY
    }

    private fun begin(kind: String, title: String) {
        val now = System.currentTimeMillis()
        val uid = LocalDb.newUid()
        val k = if (kind == "meeting") "meeting" else "lecture"
        val name = title.trim().ifBlank {
            k.replaceFirstChar { it.uppercase() } + " " +
                DateTimeFormatter.ofPattern("MMM d, h:mm a", Locale.US).format(Instant.ofEpochMilli(now).atZone(ZoneId.systemDefault()))
        }
        val file = File(filesDir, "recordings").apply { mkdirs() }.resolve("$uid.aac")
        val rec = LocalRecording(uid, k, name, now, 0, file.path, "recording")
        val n = notification(rec)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) startForeground(ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        else startForeground(ID, n)
        val mr = (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) MediaRecorder(this) else @Suppress("DEPRECATION") MediaRecorder())
        try {
            mr.setAudioSource(MediaRecorder.AudioSource.MIC)
            mr.setOutputFormat(MediaRecorder.OutputFormat.AAC_ADTS)
            mr.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            mr.setAudioSamplingRate(16_000)
            mr.setAudioChannels(1)
            mr.setAudioEncodingBitRate(32_000)
            mr.setOutputFile(file.path)
            mr.prepare()
            mr.start()
        } catch (e: Exception) {
            mr.release()
            file.delete()
            error.value = "Couldn't start recording: ${e.message}"
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return
        }
        recorder = mr
        current = rec
        instance = this
        error.value = ""
        LocalDb.get(this).put(rec)
        active.value = rec
    }

    /** Stop: the recording waits for the laptop (or is dropped if it's too short to be anything). */
    fun finish() {
        val rec = current ?: return stopSelf()
        current = null
        val ok = runCatching { recorder?.stop() }.isSuccess          // throws when nothing was captured
        recorder?.release()
        recorder = null
        val file = File(rec.file)
        val seconds = (System.currentTimeMillis() - rec.startedMs) / 1000
        val db = LocalDb.get(this)
        if (!ok || !file.exists() || file.length() < 2_000 || seconds < 3) {
            file.delete()
            db.delete("recordings", rec.uid)
        } else {
            db.put(rec.copy(durationS = seconds, state = "waiting"))
            Sync.trigger(this)
        }
        active.value = null
        instance = null
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        if (current != null) finish()
        super.onDestroy()
    }

    private fun notification(rec: LocalRecording) = NotificationCompat.Builder(this, MaxApp.CH_RECORD)
        .setSmallIcon(R.drawable.ic_stat_max)
        .setContentTitle("● Recording ${rec.kind}")
        .setContentText(rec.title + " · notes are written on the laptop")
        .setOngoing(true)
        .setSilent(true)
        .setUsesChronometer(true)
        .setWhen(rec.startedMs)
        .setContentIntent(PendingIntent.getActivity(this, 0,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
        .addAction(0, "Stop", PendingIntent.getBroadcast(this, 1, Intent(this, StopRecordingReceiver::class.java),
                                                         PendingIntent.FLAG_IMMUTABLE))
        .build()

    companion object {
        const val ID = 3
        const val ACTION_START = "start"
        @Volatile private var instance: RecordService? = null
        /** The recording in progress (for the Notes screen), and the last start error. */
        val active = MutableStateFlow<LocalRecording?>(null)
        val error = MutableStateFlow("")

        fun start(context: Context, kind: String, title: String = "") {
            ContextCompat.startForegroundService(context, Intent(context, RecordService::class.java)
                .setAction(ACTION_START).putExtra("kind", kind).putExtra("title", title))
        }

        fun stop() { instance?.finish() }

        /** After the app was killed mid-recording: what was written is kept and waits for the laptop. */
        fun recover(context: Context) {
            if (instance != null) return
            val db = LocalDb.get(context)
            for (r in db.recordings("state = 'recording'")) {
                val f = File(r.file)
                if (f.exists() && f.length() > 2_000) db.put(r.copy(state = "waiting", durationS = f.length() / 4_000))
                else { f.delete(); db.delete("recordings", r.uid) }
            }
            context.getSystemService(NotificationManager::class.java).cancel(ID)
        }
    }
}

/** The recording notification's Stop button. */
class StopRecordingReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) = RecordService.stop()
}
