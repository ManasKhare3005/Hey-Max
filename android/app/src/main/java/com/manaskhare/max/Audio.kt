package com.manaskhare.max

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaPlayer
import android.media.MediaRecorder
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.sqrt

/**
 * Push-to-talk recorder: 16 kHz mono PCM16, the format Whisper wants. Stops by itself after
 * ~1.2 s of silence once you've spoken (or at 30 s), like the laptop's listener.
 */
class Recorder(private val onAutoStop: () -> Unit) {
    @Volatile var level = 0f            // 0..1 for the orb
        private set
    @Volatile private var running = false
    private var record: AudioRecord? = null
    private var thread: Thread? = null
    private val pcm = ByteArrayOutputStream()

    @SuppressLint("MissingPermission")  // the UI asks for RECORD_AUDIO first
    fun start() {
        val min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val rec = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE, AudioFormat.CHANNEL_IN_MONO,
                              AudioFormat.ENCODING_PCM_16BIT, maxOf(min, RATE / 2))
        record = rec
        synchronized(pcm) { pcm.reset() }
        rec.startRecording()
        running = true
        thread = Thread {
            val buf = ByteArray(RATE / 10 * 2)          // 100 ms
            var noise = 0.0
            var frames = 0
            var spoke = false
            var quiet = 0
            while (running) {
                val n = rec.read(buf, 0, buf.size)
                if (n <= 0) continue
                synchronized(pcm) { pcm.write(buf, 0, n) }
                val r = rms(buf, n)
                level = (r / 3000.0).coerceIn(0.0, 1.0).toFloat()
                frames++
                if (frames <= 3) { noise = maxOf(noise, r); continue }      // first 300 ms = room noise
                val loud = r > maxOf(noise * 2.5, 350.0)
                if (loud) { spoke = true; quiet = 0 } else if (spoke) quiet++
                if ((spoke && quiet >= 12) || frames >= 300) {              // 1.2 s silence / 30 s max
                    running = false
                    onAutoStop()
                }
            }
        }.apply { start() }
    }

    val isRecording get() = record != null

    /** Stops and returns a WAV file's bytes (empty if nothing was recorded). */
    fun stop(): ByteArray {
        running = false
        thread?.join(600)
        record?.runCatching { stop(); release() }
        record = null
        level = 0f
        val data = synchronized(pcm) { pcm.toByteArray() }
        return if (data.size < RATE / 5) ByteArray(0) else wav(data)   // < 100 ms: nothing said
    }

    private fun rms(b: ByteArray, n: Int): Double {
        val s = ByteBuffer.wrap(b, 0, n).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer()
        var sum = 0.0
        val count = s.remaining()
        while (s.hasRemaining()) { val v = s.get().toDouble(); sum += v * v }
        return if (count == 0) 0.0 else sqrt(sum / count)
    }

    companion object {
        const val RATE = 16_000

        fun wav(pcm: ByteArray, rate: Int = RATE): ByteArray {
            val header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN).apply {
                put("RIFF".toByteArray()); putInt(36 + pcm.size); put("WAVE".toByteArray())
                put("fmt ".toByteArray()); putInt(16); putShort(1); putShort(1)
                putInt(rate); putInt(rate * 2); putShort(2); putShort(16)
                put("data".toByteArray()); putInt(pcm.size)
            }
            return header.array() + pcm
        }
    }
}

/** Plays Max's reply (WAV from the laptop's Piper voice). */
class Player(private val context: Context) {
    private var player: MediaPlayer? = null

    fun play(wav: ByteArray, onDone: () -> Unit) {
        stop()
        val file = File(context.cacheDir, "reply.wav").apply { writeBytes(wav) }
        player = MediaPlayer().apply {
            setAudioAttributes(AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ASSISTANT)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            setDataSource(file.path)
            setOnCompletionListener { stop(); onDone() }
            setOnErrorListener { _, _, _ -> stop(); onDone(); true }
            prepare()
            start()
        }
    }

    fun stop() {
        player?.runCatching { stop(); release() }
        player = null
    }
}
