package com.manaskhare.max.ui

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import com.manaskhare.max.Phase
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/** The live voice orb: breathes when idle, follows your voice, spins while thinking. */
@Composable
fun Orb(phase: Phase, level: Float, modifier: Modifier = Modifier) {
    val t = rememberInfiniteTransition(label = "orb")
    val spin by t.animateFloat(0f, 360f, infiniteRepeatable(tween(6000, easing = LinearEasing)), label = "spin")
    val breath by t.animateFloat(0f, 1f, infiniteRepeatable(tween(2600), RepeatMode.Reverse), label = "breath")
    val voice by animateFloatAsState(if (phase == Phase.LISTENING) level else 0f, tween(90), label = "voice")
    val tint = when (phase) {
        Phase.LISTENING -> Color(0xFF67E8F9)
        Phase.THINKING -> Color(0xFFA78BFA)
        Phase.SPEAKING -> Color(0xFF34D399)
        Phase.IDLE -> Cyan
    }

    Canvas(modifier) {
        val c = center
        val r = size.minDimension * 0.30f
        val pulse = when (phase) {
            Phase.IDLE -> 0.03f * breath
            Phase.LISTENING -> 0.05f + 0.35f * voice
            Phase.THINKING -> 0.06f * breath
            Phase.SPEAKING -> 0.08f + 0.06f * breath
        }
        // glow
        drawCircle(Brush.radialGradient(listOf(tint.copy(alpha = 0.35f), Color.Transparent), c, r * 2.1f), r * 2.1f, c)
        // core
        drawCircle(Brush.radialGradient(listOf(Color.White.copy(alpha = 0.9f), tint, tint.copy(alpha = 0.15f)), c, r * (1 + pulse)),
                   r * (1 + pulse), c)
        // rings
        for (i in 0 until 3) {
            val rr = r * (1.35f + i * 0.28f) + r * pulse * (i + 1) * 0.4f
            drawCircle(tint.copy(alpha = 0.28f - i * 0.07f), rr, c, style = Stroke(width = 2f))
        }
        // orbiting ticks (faster while thinking)
        val speed = if (phase == Phase.THINKING) 3f else 1f
        for (i in 0 until 3) {
            val a = ((spin * speed + i * 120f) % 360f) * PI.toFloat() / 180f
            val rr = r * 1.63f
            drawCircle(tint, 4.5f, Offset(c.x + rr * cos(a), c.y + rr * sin(a)))
        }
    }
}
