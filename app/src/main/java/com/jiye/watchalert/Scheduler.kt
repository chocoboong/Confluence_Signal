package com.jiye.watchalert

import android.content.Context
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequest
import androidx.work.WorkManager
import java.util.Calendar
import java.util.TimeZone
import java.util.concurrent.TimeUnit

/**
 * 자동 실행 관리.
 *
 * 정각을 고집하지 않는다. 안드로이드는 절전 때문에 예약 시각을 그대로 지켜 주지
 * 않으므로, 4시간마다 깨어나서 '지금 돌아야 하는가'를 스스로 판단하는 편이 훨씬
 * 잘 버틴다. 한 번 놓쳐도 다음 깨어남이 알아서 채운다.
 */
object Scheduler {
    private const val WORK = "watchalert-periodic"
    val KST: TimeZone = TimeZone.getTimeZone("Asia/Seoul")

    fun enable(c: Context) {
        val req = PeriodicWorkRequest.Builder(ScanWorker::class.java, 4, TimeUnit.HOURS)
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build()
            )
            .build()
        WorkManager.getInstance(c).enqueueUniquePeriodicWork(
            WORK, ExistingPeriodicWorkPolicy.UPDATE, req
        )
    }

    fun disable(c: Context) {
        WorkManager.getInstance(c).cancelUniqueWork(WORK)
    }

    fun todayKst(): String {
        val cal = Calendar.getInstance(KST)
        return String.format(
            "%04d-%02d-%02d",
            cal.get(Calendar.YEAR), cal.get(Calendar.MONTH) + 1, cal.get(Calendar.DAY_OF_MONTH)
        )
    }

    /**
     * 지금 실제로 돌아야 하는지.
     *
     *  - 한국시간 07시 이후여야 한다. 미국장은 한국시간 새벽 5~6시에 닫힌다.
     *  - 화~토 만 의미가 있다. 일·월은 새로 닫힌 미국장이 없다.
     *  - 오늘 이미 성공했으면 다시 돌지 않는다.
     */
    fun shouldRunNow(c: Context): Pair<Boolean, String> {
        val cal = Calendar.getInstance(KST)
        val hour = cal.get(Calendar.HOUR_OF_DAY)
        val dow = cal.get(Calendar.DAY_OF_WEEK)   // 1=일 ... 7=토
        if (hour < 7) return false to "아직 07시 전"
        if (dow == Calendar.SUNDAY || dow == Calendar.MONDAY)
            return false to "일·월은 새로 닫힌 미국장이 없음"
        if (Prefs.getStr(c, Prefs.K_LAST_OK) == todayKst())
            return false to "오늘 이미 완료"
        if (Prefs.getStr(c, Prefs.K_TOKEN).isBlank() || Prefs.getStr(c, Prefs.K_CHAT).isBlank())
            return false to "텔레그램 설정이 비어 있음"
        if (Prefs.getStr(c, Prefs.K_TICKERS).isBlank())
            return false to "관찰 종목이 비어 있음"
        return true to "실행"
    }
}
