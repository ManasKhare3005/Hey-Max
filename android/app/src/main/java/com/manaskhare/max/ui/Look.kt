
package com.manaskhare.max.ui

import android.content.Context
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import com.manaskhare.max.R
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update

/** Colourways: one accent plus the background, text and core-glow colours that suit it. */
enum class Accent(
    val label: String, val accent: Color, val deep: Color, val bgTop: Color, val base: Color,
    val text: Color, val muted: Color, val navOff: Color, val pill: Color, val core: Color, val glow: Color,
) {
    GOLD("Gold", Color(0xFFF2C46D), Color(0xFF8A6420), Color(0xFF1D1810), Color(0xFF0A0907),
         Color(0xFFF7F3EA), Color(0xFFA89E8C), Color(0xFF988F7E), Color(0xFFF7DFA8), Color(0xFFCFF3FF), Color(0xFFAAE6FF)),
    ROSE("Rose", Color(0xFFFF8A9B), Color(0xFF9C3A4B), Color(0xFF24121A), Color(0xFF0A0809),
         Color(0xFFF8F1F2), Color(0xFFA8999D), Color(0xFF8E8285), Color(0xFFFFD3DA), Color(0xFFFFE1E6), Color(0xFFFFB4C3)),
    ICE("Ice", Color(0xFF8FD3FF), Color(0xFF2F6E99), Color(0xFF14202E), Color(0xFF080B10),
        Color(0xFFF4F6FA), Color(0xFF9AA3B2), Color(0xFF8B95A5), Color(0xFFD6EEFF), Color(0xFFE3F5FF), Color(0xFFAADCFF)),
    EMERALD("Emerald", Color(0xFF3EE6C1), Color(0xFF137A63), Color(0xFF0E1F1B), Color(0xFF060A0B),
            Color(0xFFF1F7F6), Color(0xFF93A8A3), Color(0xFF8FA3A0), Color(0xFFBFF5E7), Color(0xFFDFFFF6), Color(0xFF96FFDC)),
    VIOLET("Violet", Color(0xFFB4A7FF), Color(0xFF4F3FB0), Color(0xFF1A1530), Color(0xFF09080F),
           Color(0xFFF5F3FA), Color(0xFFA49EB8), Color(0xFF8E88A3), Color(0xFFE2DCFF), Color(0xFFECE8FF), Color(0xFFC8BEFF)),
    PLATINUM("Platinum", Color(0xFFD8DEE9), Color(0xFF6B7280), Color(0xFF1A1C21), Color(0xFF0A0A0C),
             Color(0xFFF5F6F8), Color(0xFF9EA3AD), Color(0xFF8C919B), Color(0xFFF0F2F6), Color(0xFFFFFFFF), Color(0xFFDCE6FF)),
}

enum class Centrepiece(val label: String) { REACTOR("Reactor"), WAVE("Waveform"), WORDS("Words") }

enum class AnswerSize(val label: String, val sp: Int, val wordsSp: Int) { S("S", 28, 34), M("M", 34, 40), L("L", 40, 46) }

enum class Glow(val label: String, val strength: Float) { SUBTLE("Subtle", 0.45f), NORMAL("Normal", 1f), STRONG("Strong", 1.8f) }

// Fixed-weight files (one per weight) render the same on every phone; all are SIL Open Font Licence.
private fun italic(id: Int, w: Int) = Font(id, FontWeight(w), FontStyle.Italic)

private val Manrope = FontFamily(Font(R.font.manrope_400, FontWeight.Normal), Font(R.font.manrope_500, FontWeight.Medium),
                                 Font(R.font.manrope_600, FontWeight.SemiBold), Font(R.font.manrope_700, FontWeight.Bold))
private val DmSans = FontFamily(Font(R.font.dm_sans_400, FontWeight.Normal), Font(R.font.dm_sans_500, FontWeight.Medium),
                                Font(R.font.dm_sans_600, FontWeight.SemiBold), Font(R.font.dm_sans_700, FontWeight.Bold))
private val Outfit = FontFamily(Font(R.font.outfit_400, FontWeight.Normal), Font(R.font.outfit_500, FontWeight.Medium),
                                Font(R.font.outfit_600, FontWeight.SemiBold), Font(R.font.outfit_700, FontWeight.Bold))
private val SpaceGrotesk = FontFamily(Font(R.font.space_grotesk_400, FontWeight.Normal), Font(R.font.space_grotesk_500, FontWeight.Medium),
                                      Font(R.font.space_grotesk_600, FontWeight.SemiBold), Font(R.font.space_grotesk_700, FontWeight.Bold))
private val Jakarta = FontFamily(Font(R.font.plus_jakarta_400, FontWeight.Normal), Font(R.font.plus_jakarta_500, FontWeight.Medium),
                                 Font(R.font.plus_jakarta_600, FontWeight.SemiBold), Font(R.font.plus_jakarta_700, FontWeight.Bold),
                                 italic(R.font.plus_jakarta_i400, 400), italic(R.font.plus_jakarta_i500, 500))
private val InstrumentSerif = FontFamily(Font(R.font.instrument_serif), Font(R.font.instrument_serif_italic, style = FontStyle.Italic))
private val DmSerif = FontFamily(Font(R.font.dm_serif), Font(R.font.dm_serif_italic, style = FontStyle.Italic))
private val Fraunces = FontFamily(Font(R.font.fraunces_400, FontWeight.Normal), Font(R.font.fraunces_500, FontWeight.Medium),
                                  italic(R.font.fraunces_i400, 400))
private val Sora = FontFamily(Font(R.font.sora_300, FontWeight.Light), Font(R.font.sora_400, FontWeight.Normal),
                              Font(R.font.sora_600, FontWeight.SemiBold))

/** Font themes: a display face (title, Max's answers, headings) and a body face (everything else). */
enum class FontTheme(val label: String, val display: FontFamily, val body: FontFamily, val displayWeight: FontWeight) {
    CLASSIC("Classic", InstrumentSerif, Manrope, FontWeight.Normal),
    ELEGANT("Elegant", Fraunces, DmSans, FontWeight.Normal),
    EDITORIAL("Editorial", DmSerif, DmSans, FontWeight.Normal),
    MODERN("Modern", Sora, Manrope, FontWeight.Light),
    GEOMETRIC("Geometric", Outfit, Outfit, FontWeight.Normal),
    TECH("Tech", SpaceGrotesk, SpaceGrotesk, FontWeight.Medium),
    SOFT("Soft", Jakarta, Jakarta, FontWeight.Medium),
}

/** Everything the Appearance screen controls. Default: gold reactor, classic fonts. */
data class Look(
    val accent: Accent = Accent.GOLD,
    val piece: Centrepiece = Centrepiece.REACTOR,
    val size: AnswerSize = AnswerSize.M,
    val glow: Glow = Glow.NORMAL,
    val motion: Boolean = true,
    val ringBars: Boolean = false,
    val boldBars: Boolean = false,
    val font: FontTheme = FontTheme.CLASSIC,
)

val LocalLook = staticCompositionLocalOf { Look() }

/** The current look, saved on the phone; every screen recomposes when it changes. */
object Looks {
    val state = MutableStateFlow(Look())

    private inline fun <reified E : Enum<E>> pick(name: String?, fallback: E): E =
        enumValues<E>().firstOrNull { it.name == name } ?: fallback

    fun load(context: Context) {
        val sp = context.getSharedPreferences("look", Context.MODE_PRIVATE)
        val d = Look()
        state.value = Look(
            accent = pick(sp.getString("accent", null), d.accent),
            piece = pick(sp.getString("piece", null), d.piece),
            size = pick(sp.getString("size", null), d.size),
            glow = pick(sp.getString("glow", null), d.glow),
            motion = sp.getBoolean("motion", d.motion),
            ringBars = sp.getBoolean("ringBars", d.ringBars),
            boldBars = sp.getBoolean("boldBars", d.boldBars),
            font = pick(sp.getString("font", null), d.font),
        )
    }

    fun update(context: Context, change: (Look) -> Look) {
        state.update(change)
        val l = state.value
        context.getSharedPreferences("look", Context.MODE_PRIVATE).edit()
            .putString("accent", l.accent.name).putString("piece", l.piece.name)
            .putString("size", l.size.name).putString("glow", l.glow.name)
            .putBoolean("motion", l.motion).putBoolean("ringBars", l.ringBars)
            .putBoolean("boldBars", l.boldBars).putString("font", l.font.name)
            .apply()
    }
}
