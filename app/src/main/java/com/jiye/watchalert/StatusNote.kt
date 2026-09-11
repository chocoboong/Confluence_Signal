package com.jiye.watchalert

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat

/**
 * 상태바에 항상 떠 있는 '감시 중' 표시.
 *
 * 자동 실행이 켜져 있는 동안에만 보인다. 소리도 진동도 없고 지울 수도 없게 해서
 * (ongoing) 화면 위쪽에 아이콘 하나로 남는다. 눌러서 열면 앱이 뜬다.
 *
 * 알림을 다시 그려야 하는 시점은 셋이다.
 *   1) 앱 화면을 열거나 설정을 저장했을 때
 *   2) 스캔이 끝났을 때 (마지막 실행 시각을 갱신)
 *   3) 타블렛을 재부팅한 뒤 (알림은 재부팅으로 사라지므로 다시 띄운다)
 */
object StatusNote {
    const val CH = "watchalert_status"
    const val NID = 1003

    fun ensureChannel(c: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = c.getSystemService(NotificationManager::class.java) ?: return
        val ch = NotificationChannel(CH, "감시 상태", NotificationManager.IMPORTANCE_LOW)
        ch.setShowBadge(false)
        ch.enableVibration(false)
        ch.setSound(null, null)
        nm.createNotificationChannel(ch)
    }

    /** 자동 실행이 켜져 있으면 띄우고, 꺼져 있으면 지운다. */
    fun refresh(c: Context) {
        val nm = c.getSystemService(NotificationManager::class.java) ?: return
        if (!Prefs.getBool(c, Prefs.K_AUTO, false)) {
            try {
                nm.cancel(NID)
            } catch (e: Exception) {
            }
            return
        }
        ensureChannel(c)

        val n = Prefs.getStr(c, Prefs.K_TICKERS)
            .split(',', ' ', '\n', '\t')
            .map { it.trim() }
            .count { it.isNotEmpty() }

        val lastOk = Prefs.getStr(c, Prefs.K_LAST_OK)
        val lastLine = if (lastOk.isBlank()) "아직 실행 전"
        else if (lastOk == Scheduler.todayKst()) "오늘 확인 완료"
        else "마지막 확인 $lastOk"

        val (_, why) = Scheduler.shouldRunNow(c)
        val detail = "$lastLine · $why"

        val open = PendingIntent.getActivity(
            c, 0,
            Intent(c, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val note = NotificationCompat.Builder(c, CH)
            .setSmallIcon(android.R.drawable.presence_online)
            .setContentTitle("합류신호 감시 중 · 종목 ${n}개")
            .setContentText(detail)
            .setStyle(NotificationCompat.BigTextStyle().bigText(detail))
            .setOngoing(true)
            .setSilent(true)
            .setShowWhen(false)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(open)
            .build()

        try {
            nm.notify(NID, note)
        } catch (e: Exception) {
            // 알림 권한이 없으면 조용히 넘어간다.
        }
    }
}
