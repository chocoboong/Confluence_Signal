package com.jiye.watchalert

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * 타블렛을 껐다 켜면 상태바 알림이 사라진다. 예약 자체는 안드로이드가 기억하지만
 * 표시는 다시 그려 줘야 하므로 부팅 직후 한 번 띄운다. 예약도 한 번 더 확인한다.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(c: Context, intent: Intent) {
        val a = intent.action ?: return
        if (a != Intent.ACTION_BOOT_COMPLETED &&
            a != "android.intent.action.QUICKBOOT_POWERON" &&
            a != "android.intent.action.MY_PACKAGE_REPLACED"
        ) return
        try {
            ScanWorker.ensureChannels(c)
            if (Prefs.getBool(c, Prefs.K_AUTO, false)) {
                Scheduler.enable(c)
                StatusNote.refresh(c)
            }
        } catch (e: Throwable) {
            // 부팅 직후 실패해도 앱을 한 번 열면 복구된다.
        }
    }
}
