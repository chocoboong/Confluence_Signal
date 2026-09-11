package com.jiye.watchalert

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.ServiceInfo
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.work.ForegroundInfo
import androidx.work.Worker
import androidx.work.WorkerParameters

class ScanWorker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {

    companion object {
        const val CH_RUN = "watchalert_run"
        const val CH_MSG = "watchalert_msg"
        const val NID_RUN = 1001
        const val NID_MSG = 1002

        fun ensureChannels(c: Context) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
            val nm = c.getSystemService(NotificationManager::class.java) ?: return
            nm.createNotificationChannel(
                NotificationChannel(CH_RUN, "실행 중", NotificationManager.IMPORTANCE_LOW)
            )
            nm.createNotificationChannel(
                NotificationChannel(CH_MSG, "실행 결과", NotificationManager.IMPORTANCE_DEFAULT)
            )
            StatusNote.ensureChannel(c)
        }
    }

    override fun doWork(): Result {
        val c = applicationContext
        ensureChannels(c)

        StatusNote.refresh(c)
        val (should, why) = Scheduler.shouldRunNow(c)
        if (!should) return Result.success()

        try {
            val note = NotificationCompat.Builder(c, CH_RUN)
                .setSmallIcon(android.R.drawable.stat_notify_sync)
                .setContentTitle("합류신호 확인 중")
                .setContentText("야후에서 일봉을 받는 중입니다")
                .setOngoing(true)
                .build()
            // Android 10 부터는 전경 서비스의 '종류'를 밝혀야 하고,
            // Android 14 부터는 안 밝히면 예외가 난다.
            val info = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                ForegroundInfo(NID_RUN, note, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
            } else {
                ForegroundInfo(NID_RUN, note)
            }
            setForegroundAsync(info)
        } catch (e: Throwable) {
            // 전경 서비스로 못 올려도 스캔 자체는 계속한다.
        }

        return try {
            val res = PyRunner.run(c, "scan")
            val ok = res.optBoolean("ok", false)
            val summary = res.optString("summary", "")
            Prefs.put(c, Prefs.K_LAST_SUM, "${Scheduler.todayKst()} $summary")
            if (ok) {
                Prefs.put(c, Prefs.K_LAST_OK, Scheduler.todayKst())
                StatusNote.refresh(c)
                Result.success()
            } else {
                StatusNote.refresh(c)
                notify(c, "합류신호 알림 - 실패", summary.take(180))
                Result.retry()
            }
        } catch (e: Throwable) {
            notify(c, "합류신호 알림 - 오류", (e.message ?: e.toString()).take(180))
            Result.retry()
        }
    }

    private fun notify(c: Context, title: String, text: String) {
        try {
            val nm = c.getSystemService(NotificationManager::class.java) ?: return
            nm.notify(
                NID_MSG,
                NotificationCompat.Builder(c, CH_MSG)
                    .setSmallIcon(android.R.drawable.stat_notify_error)
                    .setContentTitle(title)
                    .setContentText(text)
                    .setStyle(NotificationCompat.BigTextStyle().bigText(text))
                    .setAutoCancel(true)
                    .build()
            )
        } catch (e: Exception) {
            // 알림 권한이 없으면 조용히 넘어간다.
        }
    }
}
