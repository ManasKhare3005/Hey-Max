package com.manaskhare.max.ui

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import com.manaskhare.max.Phase
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.sin

/** Seconds since first shown (0 when animation is off), plus a spin angle whose speed can change. */
private class Clock { val t = mutableFloatStateOf(0f); val spin = mutableFloatStateOf(0f) }

@Composable
private fun rememberClock(running: Boolean, spinSpeed: Float): Clock {
    val clock = remember { Clock() }
    val speed by rememberUpdatedState(spinSpeed)
    LaunchedEffect(running) {
        if (!running) return@LaunchedEffect
        var last = 0L
        while (true) {
            withFrameNanos { now ->
                if (last != 0L) {
                    val dt = (now - last) / 1e9f
                    clock.t.floatValue += dt
                    clock.spin.floatValue = (clock.spin.floatValue + dt * 16f * speed) % 360f
                }
                last = now
            }
        }
    }
    return clock
}

/** The main visual on the Max screen: reactor rings, a voice waveform, or nothing (Words). */
@Composable
fun Centrepiece(phase: Phase, level: Float, modifier: Modifier = Modifier) {
    when (LocalLook.current.piece) {
        Centrepiece.REACTOR -> Reactor(phase, level, modifier)
        Centrepiece.WAVE -> Waveform(phase, level, modifier)
        Centrepiece.WORDS -> Unit
    }
}

@Composable
fun Reactor(phase: Phase, level: Float, modifier: Modifier = Modifier) {
    val look = LocalLook.current
    val a = look.accent
    val g = look.glow.strength
    val voice by animateFloatAsState(if (phase == Phase.LISTENING) level else 0f, tween(90), label = "voice")
    val clock = rememberClock(look.motion, if (phase == Phase.THINKING) 3.5f else 1f)
    val t = clock.t.floatValue
    val spin = clock.spin.floatValue

    Canvas(modifier) {
        val s = size.minDimension / 330f
        val c = center
        val pulse = when (phase) {
            Phase.LISTENING -> 1f + 0.35f * voice
            Phase.SPEAKING -> 1f + 0.07f * abs(sin(t * 5f))
            else -> 1f + 0.04f * sin(t * 2f)
        }
        // voice bars around the ring (optional)
        if (look.ringBars) {
            for (i in 0 until 64) {
                val base = 5f + 17f * abs(sin(i * 0.61f) * cos(i * 0.23f + 0.4f))
                val live = when (phase) {
                    Phase.LISTENING -> 0.35f + voice * 1.4f * (0.6f + 0.4f * sin(t * 9f + i))
                    Phase.SPEAKING -> 0.5f + 0.5f * abs(sin(t * 6f + i * 0.5f))
                    else -> if (look.motion) 0.55f + 0.45f * abs(sin(t * 1.4f + i * 0.13f)) else 0.8f
                }
                val len = base * live * s
                val ang = (i / 64f) * 2f * PI.toFloat()
                val dir = Offset(sin(ang), -cos(ang))
                val r = 150f * s
                drawLine(a.accent.copy(alpha = (0.35f + 0.5f * live).coerceAtMost(1f)),
                         c + dir * (r - len / 2), c + dir * (r + len / 2), strokeWidth = 3f * s, cap = StrokeCap.Round)
            }
        }
        // outer slanted arcs (top and bottom), slowly turning
        rotate(20f + spin) {
            arcs(c, 130f * s, listOf(-135f to 90f, 45f to 90f), a.accent.copy(alpha = 0.55f), 2f * s, 0f)
        }
        drawCircle(a.accent.copy(alpha = 0.22f), 111f * s, c, style = Stroke(1f * s))
        // thick arc: bright sides, dim bottom, open top; turns the other way
        rotate(-35f - spin * 1.6f) {
            arcs(c, 95f * s, listOf(-45f to 90f, 135f to 90f), a.accent, 4f * s, 0.25f * g)
            arcs(c, 95f * s, listOf(45f to 90f), a.accent.copy(alpha = 0.35f), 4f * s, 0f)
        }
        drawCircle(a.glow.copy(alpha = 0.45f), 69f * s, c,
                   style = Stroke(1f * s, pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f * s, 6f * s))))
        // core: soft halo, then the bright centre
        val core = 37f * s * pulse
        drawCircle(Brush.radialGradient(listOf(a.glow.copy(alpha = (0.55f * g).coerceAtMost(1f)), Color.Transparent), c, core * 3f), core * 3f, c)
        drawCircle(Brush.radialGradient(listOf(a.accent.copy(alpha = 0.25f * g), Color.Transparent), c, core * 4.5f), core * 4.5f, c)
        drawCircle(Brush.radialGradient(0f to Color.White, 0.4f to a.core, 1f to a.glow.copy(alpha = 0.2f), center = c, radius = core), core, c)
    }
}

private fun DrawScope.arcs(c: Offset, r: Float, spans: List<Pair<Float, Float>>, color: Color, width: Float, glow: Float) {
    val tl = Offset(c.x - r, c.y - r)
    val sz = Size(r * 2, r * 2)
    for ((start, sweep) in spans) {
        if (glow > 0f) drawArc(color.copy(alpha = (0.12f * glow).coerceAtMost(1f)), start, sweep, false, tl, sz, style = Stroke(width * 4f, cap = StrokeCap.Round))
        drawArc(color, start, sweep, false, tl, sz, style = Stroke(width))
    }
}

private val WAVE = floatArrayOf(14f, 22f, 30f, 46f, 38f, 62f, 84f, 70f, 104f, 128f, 96f, 140f, 118f, 152f, 132f, 152f,
                                118f, 140f, 96f, 128f, 104f, 70f, 84f, 62f, 38f, 46f, 30f, 22f, 14f)

@Composable
fun Waveform(phase: Phase, level: Float, modifier: Modifier = Modifier) {
    val look = LocalLook.current
    val a = look.accent
    val g = look.glow.strength
    val voice by animateFloatAsState(if (phase == Phase.LISTENING) level else 0f, tween(90), label = "voice")
    val clock = rememberClock(look.motion, 1f)
    val t = clock.t.floatValue

    Canvas(modifier) {
        val s = size.minDimension / 330f
        val barW = (if (look.boldBars) 9f else 5f) * s
        val gap = (if (look.boldBars) 6f else 5f) * s
        val total = WAVE.size * barW + (WAVE.size - 1) * gap
        var x = center.x - total / 2
        val brush = Brush.verticalGradient(listOf(a.pill, a.accent), startY = center.y - 80f * s, endY = center.y + 80f * s)
        WAVE.forEachIndexed { i, base ->
            val f = when (phase) {
                Phase.LISTENING -> 0.22f + voice * 1.1f * (0.6f + 0.4f * sin(t * 9f + i))
                Phase.THINKING -> 0.3f + 0.4f * (sin(t * 4f - i * 0.45f) + 1f) / 2f
                Phase.SPEAKING -> 0.45f + 0.4f * abs(sin(t * 6f + i * 0.7f))
                Phase.IDLE -> if (look.motion) 0.55f + 0.1f * sin(t * 1.6f + i * 0.3f) else 0.6f
            }.coerceIn(0.08f, 1.1f)
            val h = base * f * s
            val top = center.y - h / 2
            if (g > 0.5f) drawRoundRect(a.accent.copy(alpha = 0.10f * g), Offset(x - barW, top - barW), Size(barW * 3, h + barW * 2), CornerRadius(barW * 1.5f))
            drawRoundRect(brush, Offset(x, top), Size(barW, h), CornerRadius(barW / 2), alpha = 0.45f + 0.55f * base / 152f)
            x += barW + gap
        }
    }
}
