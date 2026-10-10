package com.manaskhare.max

import android.app.Application
import android.content.Context
import androidx.compose.runtime.Composable
import app.cash.paparazzi.DeviceConfig
import app.cash.paparazzi.Paparazzi
import com.manaskhare.max.ui.Accent
import com.manaskhare.max.ui.AppBackground
import com.manaskhare.max.ui.AppearanceScreen
import com.manaskhare.max.ui.Centrepiece
import com.manaskhare.max.ui.FontTheme
import com.manaskhare.max.ui.HomeScreen
import com.manaskhare.max.ui.Look
import com.manaskhare.max.ui.Looks
import com.manaskhare.max.ui.MaxTheme
import com.manaskhare.max.ui.SettingsScreen
import com.manaskhare.max.ui.TodayScreen
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.ui.unit.dp
import org.junit.Rule
import org.junit.Test

/** Renders the main screens offscreen (layoutlib) so layout can be checked without a phone.
 *  Run: gradlew recordPaparazziDebug  -> app/src/test/snapshots/images */
class ScreensTest {
    @get:Rule
    val paparazzi = Paparazzi(deviceConfig = DeviceConfig.PIXEL_6, theme = "android:Theme.Material.NoActionBar")

    private fun vm(): MainViewModel {
        val app = Application()
        android.content.ContextWrapper::class.java.getDeclaredMethod("attachBaseContext", Context::class.java).apply { isAccessible = true }
            .invoke(app, paparazzi.context)
        return MainViewModel(app).apply {
            heard.value = "what's due this week"
            reply.value = "Two things: the project report on Friday at 11:59 PM, and the SPARQL quiz on Monday."
        }
    }

    private fun shot(look: Look, content: @Composable () -> Unit) {
        Looks.state.value = look
        paparazzi.snapshot { MaxTheme { AppBackground { content() } } }
    }

    @Test fun home_gold_reactor() { val m = vm(); shot(Look()) { HomeScreen(m) { it() } } }
    @Test fun home_rose_wave() { val m = vm(); shot(Look(accent = Accent.ROSE, piece = Centrepiece.WAVE, font = FontTheme.ELEGANT)) { HomeScreen(m) { it() } } }
    @Test fun home_ice_words() { val m = vm(); shot(Look(accent = Accent.ICE, piece = Centrepiece.WORDS, font = FontTheme.MODERN)) { HomeScreen(m) { it() } } }
    @Test fun settings() {
        val m = vm()
        val owner = object : androidx.activity.result.ActivityResultRegistryOwner {   // permission prompts (not shown)
            override val activityResultRegistry = object : androidx.activity.result.ActivityResultRegistry() {
                override fun <I, O> onLaunch(requestCode: Int, contract: androidx.activity.result.contract.ActivityResultContract<I, O>,
                                             input: I, options: androidx.core.app.ActivityOptionsCompat?) {}
            }
        }
        paparazzi.unsafeUpdateConfig(deviceConfig = DeviceConfig.PIXEL_6.copy(screenHeight = 5600))   // whole page
        shot(Look()) {
            androidx.compose.runtime.CompositionLocalProvider(
                androidx.activity.compose.LocalActivityResultRegistryOwner provides owner) { SettingsScreen(m) {} }
        }
    }
    @Test fun appearance() { shot(Look(ringBars = true)) { AppearanceScreen {} } }
    @Test fun today() { val m = vm(); shot(Look(accent = Accent.EMERALD, font = FontTheme.TECH)) { TodayScreen(m) } }

    /** Laptop off: reminders, events and recordings kept on the phone (sample data; no SQLite here). */
    @Test fun offline_cards() {
        val m = vm()
        val at = { days: Long, h: Int -> Offline.ms(java.time.LocalDate.now().plusDays(days).atTime(h, 0)) }
        val reminders = listOf(LocalReminder("r1", "Submit the lab report", at(0, 21)),
                               LocalReminder("r2", "Call mom", at(1, 18), dirty = false, synced = true))
        val events = listOf(LocalEvent("e1", "Study group", at(1, 15), at(1, 16), location = "Hayden Library"),
                            LocalEvent("e2", "Career fair", at(3, 0), at(4, 0), allDay = true, dirty = false, synced = true))
        val recordings = listOf(LocalRecording("p1", "lecture", "CSE 572 week 7", at(0, 9), 4380, "x", "waiting"),
                                LocalRecording("p2", "meeting", "Project sync", at(-1, 14), 1500, "x", "processing"),
                                LocalRecording("p3", "lecture", "CSE 511 midterm review", at(-2, 10), 4800, "x", "done", noteId = 7),
                                LocalRecording("p4", "lecture", "Physics lab", at(-3, 10), 600, "x", "failed", error = "bad audio"))
        paparazzi.unsafeUpdateConfig(deviceConfig = DeviceConfig.PIXEL_6.copy(screenHeight = 4200))
        val owner = object : androidx.activity.result.ActivityResultRegistryOwner {   // permission prompts (not shown)
            override val activityResultRegistry = object : androidx.activity.result.ActivityResultRegistry() {
                override fun <I, O> onLaunch(requestCode: Int, contract: androidx.activity.result.contract.ActivityResultContract<I, O>,
                                             input: I, options: androidx.core.app.ActivityOptionsCompat?) {}
            }
        }
        shot(Look()) {
            androidx.compose.runtime.CompositionLocalProvider(androidx.activity.compose.LocalActivityResultRegistryOwner provides owner) {
            androidx.compose.foundation.layout.Column(androidx.compose.ui.Modifier.padding(16.dp)) {
                com.manaskhare.max.ui.OfflineNote()
                com.manaskhare.max.ui.CalendarCard(events)
                androidx.compose.foundation.layout.Spacer(androidx.compose.ui.Modifier.height(12.dp))
                com.manaskhare.max.ui.RemindersCard(reminders)
                androidx.compose.foundation.layout.Spacer(androidx.compose.ui.Modifier.height(12.dp))
                com.manaskhare.max.ui.PhoneRecorderCard(m, {}, recordings)
            }
            }
        }
    }
}
