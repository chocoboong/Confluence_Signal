package com.jiye.watchalert

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * 07시 알람이 울렸을 때 불린다. 스캔을 집어넣고, 내일 알람을 다시 건다.
 * 알람은 한 번 울리면 없어지기 때문에 다시 거는 것이 반드시 필요하다.
 */
class DailyAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(c: Context, intent: Intent) {
        val on = try {
            Prefs.getBool(c, Prefs.K_AUTO, false)
        } catch (e: Throwable) {
            false
        }
        if (!on) return
        try {
            ScanWorker.ensureChannels(c)
            DailyAlarm.runNow(c)
        } catch (e: Throwable) {
            // 스캔을 못 넣어도 4시간 주기가 남아 있다.
        } finally {
            DailyAlarm.schedule(c)
        }
    }
}
