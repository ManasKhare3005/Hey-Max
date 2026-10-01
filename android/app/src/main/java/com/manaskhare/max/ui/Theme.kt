package com.manaskhare.max.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.manaskhare.max.Link

// ----- colour tokens: all follow the colourway picked in Settings → Appearance -----
val Accent0: Color @Composable @ReadOnlyComposable get() = LocalLook.current.accent.accent
val Cyan: Color @Composable @ReadOnlyComposable get() = Accent0                       // the accent (name kept from v1)
val Navy: Color @Composable @ReadOnlyComposable get() = LocalLook.current.accent.base
val Panel: Color @Composable @ReadOnlyComposable get() = lerp(Navy, Accent0, 0.07f)
val PanelHi: Color @Composable @ReadOnlyComposable get() = lerp(Navy, Accent0, 0.14f)
val Line: Color @Composable @ReadOnlyComposable get() = Accent0.copy(alpha = 0.18f)
val Text1: Color @Composable @ReadOnlyComposable get() = LocalLook.current.accent.text
val Text2: Color @Composable @ReadOnlyComposable get() = LocalLook.current.accent.muted
val Dim: Color @Composable @ReadOnlyComposable get() = LocalLook.current.accent.navOff
val PillText: Color @Composable @ReadOnlyComposable get() = LocalLook.current.accent.pill
val Red = Color(0xFFF87171)
val Amber = Color(0xFFFBBF24)
val Green = Color(0xFF34D399)

val DisplayStyle: TextStyle @Composable @ReadOnlyComposable get() = LocalLook.current.font.let {
    TextStyle(fontFamily = it.display, fontWeight = it.displayWeight)
}

@Composable
fun MaxTheme(content: @Composable () -> Unit) {
    val look by Looks.state.collectAsState()
    CompositionLocalProvider(LocalLook provides look) {
        val body = look.font.body
        val base = Typography()
        val typography = Typography(
            displayLarge = base.displayLarge.copy(fontFamily = body), displayMedium = base.displayMedium.copy(fontFamily = body),
            displaySmall = base.displaySmall.copy(fontFamily = body), headlineLarge = base.headlineLarge.copy(fontFamily = body),
            headlineMedium = base.headlineMedium.copy(fontFamily = body), headlineSmall = base.headlineSmall.copy(fontFamily = body),
            titleLarge = base.titleLarge.copy(fontFamily = body), titleMedium = base.titleMedium.copy(fontFamily = body),
            titleSmall = base.titleSmall.copy(fontFamily = body), bodyLarge = base.bodyLarge.copy(fontFamily = body),
            bodyMedium = base.bodyMedium.copy(fontFamily = body), bodySmall = base.bodySmall.copy(fontFamily = body),
            labelLarge = base.labelLarge.copy(fontFamily = body), labelMedium = base.labelMedium.copy(fontFamily = body),
            labelSmall = base.labelSmall.copy(fontFamily = body),
        )
        val colors = darkColorScheme(
            primary = Accent0, onPrimary = Navy, background = Navy, onBackground = Text1,
            surface = Panel, onSurface = Text1, surfaceVariant = PanelHi, onSurfaceVariant = Text2,
            outline = Line, error = Red, secondary = Accent0, surfaceContainer = Panel,
            surfaceContainerHigh = PanelHi, surfaceContainerHighest = PanelHi,
        )
        MaterialTheme(colorScheme = colors, typography = typography, content = content)
    }
}

/** The warm glow from behind that every screen sits on. */
@Composable
fun AppBackground(modifier: Modifier = Modifier, glowAt: Float = 0.3f, content: @Composable BoxScope.() -> Unit) {
    val a = LocalLook.current.accent
    Box(
        modifier.fillMaxSize().drawBehind {
            drawRect(a.base)
            drawRect(Brush.radialGradient(
                0f to a.bgTop, 1f to a.base,
                center = Offset(size.width / 2f, size.height * glowAt), radius = size.maxDimension * 0.62f,
            ))
        },
        content = content,
    )
}

val Mono: TextStyle @Composable @ReadOnlyComposable get() = TextStyle(fontSize = 11.sp, letterSpacing = 1.6.sp, fontWeight = FontWeight.SemiBold, color = Dim)

@Composable
fun Label(text: String, modifier: Modifier = Modifier, color: Color = Text2) =
    Text(text.uppercase(), style = Mono.copy(color = color), modifier = modifier)

@Composable
fun Card(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Column(
        modifier
            .fillMaxWidth()
            .background(Color.White.copy(alpha = 0.035f), RoundedCornerShape(20.dp))
            .border(1.dp, Accent0.copy(alpha = 0.16f), RoundedCornerShape(20.dp))
            .padding(16.dp),
        content = content,
    )
}

@Composable
fun Title(text: String) = Text(text, style = DisplayStyle, color = Text1, fontSize = 34.sp)

@Composable
fun GhostButton(text: String, modifier: Modifier = Modifier, color: Color = Cyan, onClick: () -> Unit) =
    OutlinedButton(
        onClick = onClick, modifier = modifier.defaultMinSize(minHeight = 44.dp), shape = RoundedCornerShape(12.dp),
        border = BorderStroke(1.dp, color.copy(alpha = 0.45f)),
        colors = ButtonDefaults.outlinedButtonColors(contentColor = color),
    ) { Text(text, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis) }

/** The pill-shaped main action ("Tap to talk"). */
@Composable
fun PillButton(text: String, icon: ImageVector?, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Row(
        modifier
            .height(58.dp)
            .clip(RoundedCornerShape(50))
            .background(Accent0.copy(alpha = 0.12f))
            .border(1.dp, Accent0.copy(alpha = 0.6f), RoundedCornerShape(50))
            .clickable(role = Role.Button, onClick = onClick)
            .padding(horizontal = 28.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        if (icon != null) Icon(icon, null, tint = PillText, modifier = Modifier.size(22.dp))
        Text(text, color = PillText, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
fun LinkChip(link: Link) {
    val (color, text) = when (link) {
        Link.ONLINE -> Accent0 to "Laptop online"
        Link.CONNECTING -> Amber to "Connecting"
        Link.UNAUTHORIZED -> Red to "Not paired"
        Link.OFF -> Dim to "Offline"
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(7.dp).background(color, CircleShape))
        Spacer(Modifier.width(7.dp))
        Text(text, color = Text2, fontSize = 13.sp)
    }
}

// ----- settings building blocks: full-width rows, so nothing squeezes on a phone -----

@Composable
fun Section(title: String, modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Column(modifier.fillMaxWidth()) {
        Label(title, Modifier.padding(start = 6.dp, bottom = 8.dp))
        Column(
            Modifier.fillMaxWidth()
                .clip(RoundedCornerShape(20.dp))
                .background(Color.White.copy(alpha = 0.035f))
                .border(1.dp, Accent0.copy(alpha = 0.16f), RoundedCornerShape(20.dp)),
            content = content,
        )
    }
}

@Composable
fun RowDivider() = HorizontalDivider(Modifier.padding(start = 56.dp), thickness = 1.dp, color = Accent0.copy(alpha = 0.10f))

@Composable
fun SettingRow(
    title: String, icon: ImageVector? = null, subtitle: String? = null, color: Color = Text1,
    onClick: (() -> Unit)? = null, trailing: (@Composable () -> Unit)? = null,
) {
    Row(
        Modifier.fillMaxWidth()
            .defaultMinSize(minHeight = 56.dp)
            .then(if (onClick != null) Modifier.clickable(role = Role.Button, onClick = onClick) else Modifier)
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (icon != null) {
            Icon(icon, null, tint = if (color == Text1) Accent0 else color, modifier = Modifier.size(22.dp))
            Spacer(Modifier.width(18.dp))
        }
        Column(Modifier.weight(1f)) {
            Text(title, color = color, fontSize = 15.sp, fontWeight = FontWeight.Medium)
            if (subtitle != null) Text(subtitle, color = Text2, fontSize = 12.5.sp, lineHeight = 17.sp)
        }
        when {
            trailing != null -> { Spacer(Modifier.width(12.dp)); trailing() }
            onClick != null -> Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null, tint = Dim)
        }
    }
}

@Composable
fun Toggle(checked: Boolean, onChange: (Boolean) -> Unit) = Switch(
    checked = checked, onCheckedChange = onChange,
    colors = SwitchDefaults.colors(checkedThumbColor = Navy, checkedTrackColor = Accent0,
                                   uncheckedThumbColor = Text2, uncheckedTrackColor = Color.White.copy(alpha = 0.08f),
                                   uncheckedBorderColor = Color.White.copy(alpha = 0.12f)),
)

/** Small segmented control (S / M / L, Subtle / Normal / Strong). */
@Composable
fun <T> Segmented(options: List<T>, selected: T, label: (T) -> String, onPick: (T) -> Unit) {
    Row(Modifier.clip(RoundedCornerShape(12.dp)).background(Color.White.copy(alpha = 0.05f)).padding(3.dp)) {
        options.forEach { o ->
            val on = o == selected
            Box(
                Modifier.defaultMinSize(minWidth = 44.dp, minHeight = 36.dp)
                    .clip(RoundedCornerShape(9.dp))
                    .background(if (on) Accent0 else Color.Transparent)
                    .clickable(role = Role.RadioButton) { onPick(o) }
                    .padding(horizontal = 10.dp),
                contentAlignment = Alignment.Center,
            ) { Text(label(o), color = if (on) Navy else Text1, fontSize = 13.sp, fontWeight = FontWeight.SemiBold) }
        }
    }
}

private val TIME = Regex("""\b\d{1,2}(:\d{2})?\s?(AM|PM|am|pm|a\.m\.|p\.m\.)|\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|today|tonight|tomorrow|noon|midnight)\b""")

/** Max's answer with times and days picked out in the accent, in italics. */
@Composable
fun highlighted(text: String): AnnotatedString {
    val accent = Accent0
    return buildAnnotatedString {
        var last = 0
        TIME.findAll(text).forEach { m ->
            append(text.substring(last, m.range.first))
            withStyle(SpanStyle(color = accent, fontStyle = FontStyle.Italic)) { append(m.value) }
            last = m.range.last + 1
        }
        append(text.substring(last))
    }
}
