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
    @Test fun settings() { val m = vm(); shot(Look()) { SettingsScreen(m) {} } }
    @Test fun appearance() { shot(Look(ringBars = true)) { AppearanceScreen {} } }
    @Test fun today() { val m = vm(); shot(Look(accent = Accent.EMERALD, font = FontTheme.TECH)) { TodayScreen(m) } }
}
