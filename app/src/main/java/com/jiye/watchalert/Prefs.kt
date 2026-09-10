package com.jiye.watchalert

import android.content.Context
import java.io.File

/** 앱 설정. 화면에서 고친 값이 여기 저장되고, 실행할 때 파이썬이 읽는 파일로 쓰여진다. */
object Prefs {
    private const val NAME = "watchalert"

    const val K_TICKERS = "tickers"
    const val K_INDEX = "index"
    const val K_TOKEN = "token"
    const val K_CHAT = "chat"
    const val K_QUIET = "quiet"
    const val K_MAXN = "maxn"
    const val K_AUTO = "auto"
    const val K_LAST_OK = "lastOkDate"
    const val K_LAST_SUM = "lastSummary"

    private fun sp(c: Context) = c.getSharedPreferences(NAME, Context.MODE_PRIVATE)

    fun getStr(c: Context, k: String, def: String = ""): String = sp(c).getString(k, def) ?: def
    fun getBool(c: Context, k: String, def: Boolean): Boolean = sp(c).getBoolean(k, def)
    fun getInt(c: Context, k: String, def: Int): Int = sp(c).getInt(k, def)

    fun put(c: Context, k: String, v: String) = sp(c).edit().putString(k, v).apply()
    fun put(c: Context, k: String, v: Boolean) = sp(c).edit().putBoolean(k, v).apply()
    fun put(c: Context, k: String, v: Int) = sp(c).edit().putInt(k, v).apply()

    /** 데이터와 로그가 쌓이는 곳. 앱 전용 폴더라 권한이 필요 없다. */
    fun home(c: Context): String {
        val d: File = c.getExternalFilesDir(null) ?: c.filesDir
        if (!d.exists()) d.mkdirs()
        return d.absolutePath
    }
}
