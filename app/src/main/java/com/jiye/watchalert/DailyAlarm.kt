package com.jiye.watchalert

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequest
import androidx.work.WorkManager
import java.util.Calendar

/**
 * 매일 정해진 시각(한국시간 07시)에 한 번 확실히 깨우는 장치.
 *
 * 4시간 주기(Scheduler)는 그대로 두고 그 위에 얹는다. 둘 중 뭐가 먼저 돌든
 * '오늘 이미 완료' 검사가 있어서 하루 한 번만 실제로 스캔한다.
 *
 * setAndAllowWhileIdle 을 쓴다. 절전(Doze) 중에도 깨어나고, 안드로이드 12+ 에서
 * 별도 권한을 요구하지 않는다. 정확도는 수 분 단위 — 이 용도에는 충분하다.
 * (setExact 계열은 '알람 시계' 앱이 아니면 사용자가 설정에서 권한을 켜 줘야 한다.)
 *
 * 알람은 한 번 울리면 없어지므로, 울릴 때마다 다음 날 것을 다시 건다.
 * 재부팅하면 알람이 통째로 지워지므로 BootReceiver 가 다시 건다.
 */
object DailyAlarm {
    private const val REQ = 7001
    private const val ONCE = "watchalert-once"

    private fun pending(c: Context): PendingIntent {
        val i = Intent(c, DailyAlarmReceiver::class.java)
        var flags = PendingIntent.FLAG_UPDATE_CURRENT
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            flags = flags or PendingIntent.FLAG_IMMUTABLE
        }
        return PendingIntent.getBroadcast(c, REQ, i, flags)
    }

    /** 다음 07시(한국시간)까지 남은 밀리초. 이미 지났으면 내일 07시. */
    fun nextTriggerMillis(): Long {
        val now = Calendar.getInstance(Scheduler.KST)
        val t = Calendar.getInstance(Scheduler.KST)
        t.set(Calendar.HOUR_OF_DAY, Scheduler.RUN_HOUR)
        t.set(Calendar.MINUTE, 0)
        t.set(Calendar.SECOND, 0)
        t.set(Calendar.MILLISECOND, 0)
        if (!t.after(now)) t.add(Calendar.DAY_OF_MONTH, 1)
        return t.timeInMillis
    }

    fun schedule(c: Context) {
        val am = c.getSystemService(AlarmManager::class.java) ?: return
        val at = nextTriggerMillis()
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pending(c))
            } else {
                am.set(AlarmManager.RTC_WAKEUP, at, pending(c))
            }
        } catch (e: Throwable) {
            // 알람을 못 걸어도 4시간 주기가 남아 있으므로 치명적이지 않다.
        }
    }

    fun cancel(c: Context) {
        val am = c.getSystemService(AlarmManager::class.java) ?: return
        try {
            am.cancel(pending(c))
        } catch (e: Throwable) {
        }
    }

    /** 알람이 울렸을 때 실제로 스캔을 집어넣는다. */
    fun runNow(c: Context) {
        val req = OneTimeWorkRequest.Builder(ScanWorker::class.java)
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build()
            )
            .build()
        // KEEP: 마침 4시간 주기가 돌고 있으면 그걸 그대로 두고 중복 실행하지 않는다.
        WorkManager.getInstance(c).enqueueUniqueWork(ONCE, ExistingWorkPolicy.KEEP, req)
    }
}
