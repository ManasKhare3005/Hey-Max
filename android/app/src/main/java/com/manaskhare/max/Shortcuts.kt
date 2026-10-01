package com.manaskhare.max

import android.annotation.SuppressLint
import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.content.Intent
import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import android.widget.RemoteViews

fun assistIntent(context: Context): Intent =
    Intent(context, AssistActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

/** Quick Settings tile: pull down the shade, tap "Max", talk. */
class MaxTileService : TileService() {
    override fun onStartListening() {
        qsTile?.apply {
            state = if (Prefs(this@MaxTileService).paired) Tile.STATE_INACTIVE else Tile.STATE_UNAVAILABLE
            label = "Max"
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) subtitle = "Talk"
            updateTile()
        }
    }

    @SuppressLint("StartActivityAndCollapseDeprecated")
    override fun onClick() {
        val intent = assistIntent(this)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startActivityAndCollapse(PendingIntent.getActivity(this, 0, intent, PendingIntent.FLAG_IMMUTABLE))
        } else {
            @Suppress("DEPRECATION")
            startActivityAndCollapse(intent)
        }
    }
}

/** Home-screen widget: one big "Talk to Max" button. */
class MaxWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        val tap = PendingIntent.getActivity(context, 1, assistIntent(context),
                                            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val views = RemoteViews(context.packageName, R.layout.widget_max).apply {
            setOnClickPendingIntent(R.id.widget_root, tap)
        }
        ids.forEach { manager.updateAppWidget(it, views) }
    }
}
